"""情景记忆的索引：运行日志（run journal）。

## 为什么需要它（而不是直接查 checkpoint 表）

ADR-006 定了"记忆长在轨迹上"——轨迹本体是 LangGraph 的 checkpoint。
但 checkpoint 表结构由框架管理，**不提供按内容检索**的能力。
所以这里补一层**派生索引**：每次运行结束追加一条轻量记录（JSONL），
用于"我以前跑过类似目标吗？结果如何？"这类召回。

它不复制轨迹、不试图成为第二份真相：只存**召回所需的摘要 + 指回 thread_id 的锚点**。

## 为什么单独一层而不是塞进长期记忆 markdown

- 运行日志是**高频、追加式、机器生成**的；长期记忆是**低频、人工/审批、可读**的。
- 两者的写入纪律不同：日志自动写（无副作用风险），记忆必须过门禁 + 审批。
  混在一起会让"审批"这道闸门形同虚设。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class RunRecord:
    """一次运行的轻量摘要（召回用，不是轨迹本体）。"""

    run_id: str
    goal: str
    passed: bool
    steps: int
    model_kind: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    goal_fault: str = ""
    tools_used: list[str] = field(default_factory=list)
    failed_tools: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    evidence_count: int = 0
    approvals_denied: int = 0
    reasons: list[str] = field(default_factory=list)
    answer_head: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRecord:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_result(cls, res: Any) -> RunRecord:
        """从 AgentRunResult 构造（保持 run journal 与运行结果同源）。"""
        verdict = dict(getattr(res, "verdict", {}) or {})
        tools = sorted({str(e.get("tool")) for e in (getattr(res, "evidence", []) or []) if e.get("tool")})
        failed = sorted(
            {str(e.get("tool")) for e in (getattr(res, "evidence", []) or []) if e.get("tool") and not e.get("ok")}
        )
        return cls(
            run_id=str(getattr(res, "thread_id", "")),
            goal=str(getattr(res, "goal", "")),
            passed=bool(verdict.get("passed")),
            steps=int(getattr(res, "steps", 0)),
            model_kind=str(getattr(res, "model_kind", "")),
            goal_fault=str(verdict.get("goal_fault") or ""),
            tools_used=tools,
            failed_tools=failed,
            refs=list(getattr(res, "sources", []) or []),
            evidence_count=int(verdict.get("evidence_count") or 0),
            approvals_denied=int(verdict.get("approvals_denied") or 0),
            reasons=[str(r) for r in (verdict.get("reasons") or [])],
            answer_head=str(verdict.get("answer") or "")[:400],
        )


class RunJournal:
    """运行日志（JSONL，追加式）。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def under(cls, memory_dir: Path) -> RunJournal:
        return cls(Path(memory_dir) / "episodic" / "runs.jsonl")

    # ---- 写 ----

    def append(self, record: RunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    # ---- 读 ----

    def read_all(self) -> list[RunRecord]:
        if not self.path.is_file():
            return []
        out: list[RunRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(RunRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # 坏行跳过，不让一行损坏毁掉整段记忆
        return out

    def count(self) -> int:
        return len(self.read_all())

    def stats(self) -> dict[str, Any]:
        recs = self.read_all()
        if not recs:
            return {"runs": 0}
        return {
            "runs": len(recs),
            "passed": sum(1 for r in recs if r.passed),
            "failed": sum(1 for r in recs if not r.passed),
            "faults": len({r.goal_fault for r in recs if r.goal_fault}),
            "path": str(self.path),
        }


__all__ = ["RunJournal", "RunRecord"]
