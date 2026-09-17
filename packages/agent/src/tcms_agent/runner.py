"""运行器：thread_id / checkpoint 持久化 / 结果收集 / 轨迹回放。

这一层是"harness"的门面，负责三件事：
1. **持久化**：每次运行绑定一个 `thread_id`，LangGraph 的 SqliteSaver 会在每个
   超级步后落盘完整状态 —— 轨迹因此成为可回放、可续跑、可审计的资产，
   而不是跑完就消失的日志。这是情景记忆（M5）的底座。
2. **预算**：把 max_steps 与 recursion_limit 都传进图/运行时，
   两道独立的刹车（图内条件边 + LangGraph 超级步上限）。
3. **如实汇报**：model_kind（llm / offline-rule）、工具审计、引用校验结论
   全部随结果返回，不做任何美化。
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from .config import AgentConfig
from .graph import build_graph
from .knowledge import KnowledgeContext, build_knowledge


@dataclass
class AgentRunResult:
    """一次 Agent 运行的完整结果（可序列化）。"""

    thread_id: str
    goal: str
    model_kind: str
    verdict: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    trace: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    approvals: list[dict] = field(default_factory=list)
    """人工审批记录（批准/拒绝 + 理由）。空列表表示本次运行没触发过审批。"""

    memory_hits: list[dict] = field(default_factory=list)
    """本次召回到的历史记忆（结构化明细，供审计"到底想起了什么"）。"""

    @property
    def passed(self) -> bool:
        return bool(self.verdict.get("passed"))

    @property
    def answer(self) -> str:
        return str(self.verdict.get("answer") or "")

    @property
    def report(self) -> str:
        return str(self.verdict.get("report") or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "goal": self.goal,
            "model_kind": self.model_kind,
            "passed": self.passed,
            "steps": self.steps,
            "verdict": self.verdict,
            "audit": self.audit,
            "sources": self.sources,
            "evidence": self.evidence,
            "trace": self.trace,
            "approvals": self.approvals,
            "memory_hits": self.memory_hits,
        }


class AgentRunner:
    """Agent 运行器（持有知识底座与配置，可复用跑多个目标）。"""

    def __init__(self, cfg: AgentConfig | None = None, knowledge: KnowledgeContext | None = None):
        self.cfg = cfg or AgentConfig()
        self.knowledge = knowledge or build_knowledge(self.cfg.upstream)

    # ---- 内部：连接与图 ----

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.cfg.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(path), check_same_thread=False)

    def build(self, conn: sqlite3.Connection, run_id: str = "adhoc"):
        """用给定连接构建（带 checkpoint 的）图；返回 (graph, registry, model_kind)。

        run_id 会传给执行沙箱做产物归档目录名，因此同一次运行的所有执行产物
        都落在同一个目录下，便于事后整体复核。
        """
        from langgraph.checkpoint.sqlite import SqliteSaver

        saver = SqliteSaver(conn)
        return build_graph(self.knowledge, self.cfg, checkpointer=saver, run_id=run_id)

    # ---- 运行 ----

    def run(
        self,
        goal: str,
        thread_id: str | None = None,
        approver: Any = None,
    ) -> AgentRunResult:
        """跑一个目标，返回结构化结果（状态已落盘）。

        `approver`：人在环路的审批回调，签名 `(payload: dict) -> 决策`。
        决策可为 bool / {"approved": bool, "reason": str} / 上述的列表，
        与待审批项一一对应；未覆盖的项一律视为**拒绝**。

        **默认 approver=None 时全部拒绝**——安全默认：无人把关时宁可不写。
        如需放行，必须显式传入审批者（CLI 会走交互式提示）。
        """
        tid = thread_id or f"run-{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        try:
            graph, registry, model_kind = self.build(conn, run_id=tid)
            config = {
                "configurable": {"thread_id": tid},
                "recursion_limit": self.cfg.recursion_limit,
            }
            init = {
                "goal": goal,
                "run_id": tid,
                "messages": [HumanMessage(goal)],
                "steps": 0,
                "finished": False,
                "plan": [],
                "evidence": [],
                "sources": [],
                "trace": [],
                "pending": [],
                "approvals": [],
                "memory_context": "",
                "memory_hits": [],
            }
            final = graph.invoke(init, config)
            final = self._drive_approvals(graph, config, final, approver)
        finally:
            conn.close()

        result = AgentRunResult(
            thread_id=tid,
            goal=goal,
            model_kind=model_kind,
            verdict=dict(final.get("verdict") or {}),
            steps=int(final.get("steps", 0)),
            trace=list(final.get("trace") or []),
            evidence=list(final.get("evidence") or []),
            sources=list(dict.fromkeys(final.get("sources") or [])),
            audit=registry.audit_summary(),
            approvals=list(final.get("approvals") or []),
            memory_hits=list(final.get("memory_hits") or []),
        )
        self._journal(result)
        return result

    # ---- 情景记忆：运行日志（写侧）----

    def _journal(self, result: AgentRunResult) -> None:
        """把本次运行追加进运行日志（供后续召回）。失败不影响主流程。"""
        if not self.cfg.journal_enabled:
            return
        try:
            from .memory.journal import RunJournal, RunRecord

            RunJournal.under(self.cfg.memory_dir).append(RunRecord.from_result(result))
        except Exception:  # noqa: BLE001 - 日志写失败不该让一次成功的运行变成失败
            pass

    # ---- 人在环路：中断 → 征求决策 → 恢复 ----

    def _drive_approvals(self, graph: Any, config: dict, state: dict, approver) -> dict:
        """反复「取中断载荷 → 征求决策 → resume」，直到图不再暂停。

        设了硬上限（guard）：审批循环本身也可能因为 Agent 反复请求写入而变长，
        必须有刹车，且到顶要如实暴露而不是静默吞掉。
        """
        from langgraph.types import Command

        for _ in range(10):
            snap = graph.get_state(config)
            interrupts = [
                i
                for t in (snap.tasks or ())
                for i in (getattr(t, "interrupts", ()) or ())
            ]
            if not interrupts:
                break
            if len(interrupts) == 1:
                resume: Any = self._ask(approver, interrupts[0].value)
            else:
                resume = {i.id: self._ask(approver, i.value) for i in interrupts}
            state = graph.invoke(Command(resume=resume), config)
        else:
            state = dict(state)
            state.setdefault("trace", [])
            state["trace"] = list(state["trace"]) + [
                {
                    "node": "approve",
                    "event": "guard_exhausted",
                    "detail": "审批循环达到上限（10 轮），已停止继续征求审批",
                    "step": int(state.get("steps", 0)),
                    "data": {},
                }
            ]
        return state

    @staticmethod
    def _ask(approver, payload: dict) -> Any:
        """征求一次审批决策；无审批者 → 默认拒绝（安全默认）。"""
        if approver is None:
            n = len((payload or {}).get("requests") or []) or 1
            return [
                {"approved": False, "reason": "无人审批（默认拒绝持久化写入）"} for _ in range(n)
            ]
        return approver(payload)

    # ---- 轨迹回放（time-travel）----

    def history(self, thread_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """按超级步回放某次运行的状态快照（越靠后越新）。

        每条快照给出：第几步、下一个要执行的节点、当时的 trace 长度与工具审计量。
        这既是调试手段，也是"过程可审计"的证据来源。
        """
        conn = self._connect()
        try:
            graph, _registry, _kind = self.build(conn)
            snaps = list(graph.get_state_history({"configurable": {"thread_id": thread_id}}))
        finally:
            conn.close()
        out: list[dict[str, Any]] = []
        for i, s in enumerate(snaps[:limit]):
            vals = s.values or {}
            out.append(
                {
                    "index": i,
                    "next": list(s.next or ()),
                    "steps": int(vals.get("steps", 0)),
                    "trace_len": len(vals.get("trace") or []),
                    "evidence_len": len(vals.get("evidence") or []),
                    "finished": bool(vals.get("finished")),
                    "created_at": str(getattr(s, "created_at", "") or ""),
                }
            )
        return out


__all__ = ["AgentRunResult", "AgentRunner"]
