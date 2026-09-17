"""离线巩固（consolidation）：跑完一批任务后，从历史里提炼可复用的经验。

## 为什么要"离线"而不是跑的时候就写

在线写记忆是**被单次结果牵着走**的：一次侥幸通过就可能沉淀成"经验"。
离线巩固看的是**跨运行的重复模式**——只有反复出现的东西才值得写进长期记忆。
这也是 sleep-time compute 的核心直觉：把"经历"和"从中学习"分开做。

## 两道门禁（对应目标里的"写入门禁"）

1. **引用门禁**：proposal 里引用的资产 id 必须真实存在（复用 verify 的同一套校验）；
2. **证据门禁**：proposal 必须指出它来自哪些运行，且这些 run_id 必须真的在日志里。

第 2 条是这里特有的：**一条"经验"若拿不出出处的运行记录，就不配进长期记忆**。
它防的是"模型编一条听起来很对的规律"——那种东西一旦入库，会被后续召回反复强化。

## 挖掘规则（确定性，不用 LLM）

- `recurring_tool_failure`：某工具在 ≥N 次运行中失败 → 记录规避经验
- `proven_tool_path`：某故障有 ≥N 次通过运行共享同一工具序列 → 记录推荐路径
- `unparseable_goal`：≥N 次运行无法从目标解析出故障 → 记录目标表述要求

规则挖掘的好处是可复现、可回归测试；LLM 参与的版本留给后续（且同样要过这两道门禁）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .journal import RunJournal, RunRecord

MIN_OCCURRENCES = 2
_UNPARSEABLE = "无法从目标解析出真实故障键"


@dataclass
class Proposal:
    """一条待写入的巩固结果。"""

    title: str
    content: str
    kind: str  # procedural
    refs: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    pattern: str = ""
    occurrences: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 挖掘
# ---------------------------------------------------------------------------


def mine(records: list[RunRecord], *, min_occurrences: int = MIN_OCCURRENCES) -> list[Proposal]:
    """从运行日志里挖出重复模式（确定性规则）。"""
    out: list[Proposal] = []
    out += _mine_tool_failures(records, min_occurrences)
    out += _mine_proven_paths(records, min_occurrences)
    out += _mine_unparseable(records, min_occurrences)
    return out


def _mine_tool_failures(records: list[RunRecord], n: int) -> list[Proposal]:
    by_tool: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        for t in r.failed_tools:
            by_tool[t].append(r)
    out = []
    for tool, rs in sorted(by_tool.items()):
        if len(rs) < n:
            continue
        # 收集该工具在失败运行里的原因片段（去重，最多 3 条）
        samples: list[str] = []
        for r in rs:
            for reason in r.reasons or []:
                if reason not in samples:
                    samples.append(reason)
        out.append(
            Proposal(
                title=f"工具 {tool} 的失败规避",
                kind="procedural",
                content=(
                    f"经验：工具 `{tool}` 在 {len(rs)} 次运行中出现失败。\n"
                    + (f"常见原因：{'；'.join(samples[:3])}\n" if samples else "")
                    + f"可用工具序列参考：{'、'.join(rs[0].tools_used) or '（无记录）'}"
                ),
                refs=sorted({x for r in rs for x in r.refs if x.startswith(("fault:", "req:"))})[:5],
                evidence=[r.run_id for r in rs],
                pattern="recurring_tool_failure",
                occurrences=len(rs),
            )
        )
    return out


def _mine_proven_paths(records: list[RunRecord], n: int) -> list[Proposal]:
    by_fault: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        if r.passed and r.goal_fault and r.tools_used:
            by_fault[r.goal_fault].append(r)
    out = []
    for fault, rs in sorted(by_fault.items()):
        if len(rs) < n:
            continue
        seqs = Counter("、".join(r.tools_used) for r in rs)
        seq, cnt = seqs.most_common(1)[0]
        if cnt < 1:
            continue
        out.append(
            Proposal(
                title=f"验证 {fault} 的可复用工具路径",
                kind="procedural",
                content=(
                    f"经验：验证故障 `{fault}` 时，以下工具组合在 {cnt} 次运行中均达成目标：\n"
                    f"  {seq}\n"
                    f"（注意：工具顺序不影响正确性，但先查语义再真跑可减少无效执行。）"
                ),
                refs=[f"fault:{fault}"],
                evidence=[r.run_id for r in rs],
                pattern="proven_tool_path",
                occurrences=cnt,
            )
        )
    return out


def _mine_unparseable(records: list[RunRecord], n: int) -> list[Proposal]:
    rs = [r for r in records if not r.goal_fault and any(_UNPARSEABLE in x for x in (r.reasons or []))]
    if len(rs) < n:
        return []
    samples = [r.goal for r in rs][:3]
    return [
        Proposal(
            title="目标表述需锚定真实故障语义",
            kind="procedural",
            content=(
                f"经验：{len(rs)} 次运行的目标未能解析出真实故障键，导致无法验证。\n"
                f"反例目标：{'; '.join(samples)}\n"
                "要求：目标里应出现 faults.yaml 中的故障名或键（可先用 kb_search / fault_detail 确认）。"
            ),
            refs=[],
            evidence=[r.run_id for r in rs],
            pattern="unparseable_goal",
            occurrences=len(rs),
        )
    ]


# ---------------------------------------------------------------------------
# 门禁
# ---------------------------------------------------------------------------


def gate(
    proposals: list[Proposal],
    *,
    check_ref: Callable[[str], tuple[bool, str]],
    known_run_ids: set[str],
) -> tuple[list[Proposal], list[dict[str, Any]]]:
    """两道门禁：引用必须真实、证据运行必须存在。

    返回 (通过的 proposals, 被拒记录)。**被拒的也要留痕**——否则无法审计
    "为什么某条经验没进记忆"。
    """
    passed: list[Proposal] = []
    rejected: list[dict[str, Any]] = []
    for p in proposals:
        bad_refs = [r for r in p.refs if not check_ref(r)[0]]
        missing_evidence = [e for e in p.evidence if e not in known_run_ids]
        reasons: list[str] = []
        if bad_refs:
            reasons.append(f"引用不存在的资产: {bad_refs}")
        if missing_evidence:
            reasons.append(f"证据运行不存在于日志: {missing_evidence}")
        # 证据门禁特有的要求：**必须**有据可查（refs 允许为空，证据不允许）
        if not p.evidence:
            reasons.append("提案没有任何证据运行（经验必须有出处）")
        if reasons:
            rejected.append({"title": p.title, "pattern": p.pattern, "reasons": reasons})
            continue
        passed.append(p)
    return passed, rejected


# ---------------------------------------------------------------------------
# 写盘
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    import re

    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "").strip(), flags=re.UNICODE).strip("-")
    return (s or "skill")[:60]


def write_proposals(proposals: list[Proposal], memory_dir: Path) -> list[str]:
    """把通过的提案写成程序性记忆（格式与 write_memory 一致，便于统一召回）。"""
    import json

    d = Path(memory_dir) / "procedural"
    d.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for p in proposals:
        path = d / f"{_slug(p.title)}.md"
        meta = {
            "title": p.title,
            "kind": p.kind,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": "consolidation",
            "pattern": p.pattern,
            "occurrences": p.occurrences,
            "refs": p.refs,
            "evidence": p.evidence,
            "refs_verified": True,
        }
        head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
        path.write_text(f"---\n{head}\n---\n\n{p.content}\n", encoding="utf-8")
        written.append(str(path))
    return written


def consolidate(
    memory_dir: Path,
    *,
    check_ref: Callable[[str], tuple[bool, str]],
    min_occurrences: int = MIN_OCCURRENCES,
    write: bool = False,
) -> dict[str, Any]:
    """完整巩固流程：读日志 → 挖掘 → 过门禁 → （可选）写盘。"""
    journal = RunJournal.under(memory_dir)
    records = journal.read_all()
    proposals = mine(records, min_occurrences=min_occurrences)
    ok, rejected = gate(
        proposals, check_ref=check_ref, known_run_ids={r.run_id for r in records}
    )
    written: list[str] = []
    if write and ok:
        written = write_proposals(ok, memory_dir)
    return {
        "runs_scanned": len(records),
        "proposed": len(proposals),
        "accepted": len(ok),
        "rejected": len(rejected),
        "written": written,
        "accepted_titles": [p.title for p in ok],
        "rejected_detail": rejected,
        "min_occurrences": min_occurrences,
    }


__all__ = ["MIN_OCCURRENCES", "Proposal", "consolidate", "gate", "mine", "write_proposals"]
