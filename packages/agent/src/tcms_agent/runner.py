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

    def build(self, conn: sqlite3.Connection):
        """用给定连接构建（带 checkpoint 的）图；返回 (graph, registry, model_kind)。"""
        from langgraph.checkpoint.sqlite import SqliteSaver

        saver = SqliteSaver(conn)
        return build_graph(self.knowledge, self.cfg, checkpointer=saver)

    # ---- 运行 ----

    def run(self, goal: str, thread_id: str | None = None) -> AgentRunResult:
        """跑一个目标，返回结构化结果（状态已落盘）。"""
        tid = thread_id or f"run-{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        try:
            graph, registry, model_kind = self.build(conn)
            init = {
                "goal": goal,
                "messages": [HumanMessage(goal)],
                "steps": 0,
                "finished": False,
                "plan": [],
                "evidence": [],
                "sources": [],
                "trace": [],
            }
            final = graph.invoke(
                init,
                config={
                    "configurable": {"thread_id": tid},
                    "recursion_limit": self.cfg.recursion_limit,
                },
            )
        finally:
            conn.close()

        return AgentRunResult(
            thread_id=tid,
            goal=goal,
            model_kind=model_kind,
            verdict=dict(final.get("verdict") or {}),
            steps=int(final.get("steps", 0)),
            trace=list(final.get("trace") or []),
            evidence=list(final.get("evidence") or []),
            sources=list(dict.fromkeys(final.get("sources") or [])),
            audit=registry.audit_summary(),
        )

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
