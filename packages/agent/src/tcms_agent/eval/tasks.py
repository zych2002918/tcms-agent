"""评测任务集：定义"什么叫做对了"，并给出机器判定。

## 为什么任务集要带负例

一个"什么都说好"的 Agent 在正例上能拿满分。所以任务集必须同时包含**应当诚实失败**
的目标（与 TCMS 无关的请求），并把"如实拒绝"计为正确——否则评测会奖励胡说。

## 判定为什么按 kind 分而不是"passed 就对了"

不同任务的成功定义不同：
- `verify`：目标被真实验证 + 解析出的故障键正确；
- `author`：Agent **自己写**的用例真跑通过（不是"工具没报错"）；
- `diagnose`：症状诊断命中（走图谱因果链）；
- `honest_fail`：**不通过**才是对的。

把 `passed` 当成唯一标准，会把"造用例"和"诊断"这两类任务判错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DATA = Path(__file__).resolve().parent / "data" / "tasks.yaml"


@dataclass
class EvalTask:
    id: str
    kind: str  # verify | author | diagnose | honest_fail
    goal: str
    expect_fault: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "goal": self.goal, "expect_fault": self.expect_fault}


def load_tasks(path: Path | None = None) -> list[EvalTask]:
    p = Path(path) if path else DATA
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return [
        EvalTask(
            id=str(t["id"]),
            kind=str(t["kind"]),
            goal=str(t["goal"]),
            expect_fault=str(t.get("expect_fault") or ""),
        )
        for t in raw.get("tasks", [])
    ]


def _tools_used(result: Any) -> list[str]:
    return [str(e.get("tool")) for e in (getattr(result, "evidence", []) or [])]


def _outcomes(result: Any, tool: str) -> list[dict]:
    return [
        dict(e.get("outcome") or {})
        for e in (getattr(result, "evidence", []) or [])
        if e.get("tool") == tool
    ]


@dataclass
class TaskVerdict:
    """一条任务的判定结果。"""

    task_id: str
    kind: str
    matched: bool
    reason: str
    passed: bool
    steps: int
    tool_calls: int
    refs_checked: int
    refs_fabricated: int
    tool_failures: int
    duration_ms: int
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "matched": self.matched,
            "reason": self.reason,
            "passed": self.passed,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "refs_checked": self.refs_checked,
            "refs_fabricated": self.refs_fabricated,
            "tool_failures": self.tool_failures,
            "duration_ms": self.duration_ms,
            "extras": self.extras,
        }


def check(task: EvalTask, result: Any) -> TaskVerdict:
    """按 kind 判定一条任务是否达成（全部基于结构化字段，不解析自然语言）。"""
    v = dict(getattr(result, "verdict", {}) or {})
    tools = _tools_used(result)
    passed = bool(v.get("passed"))
    refs_checked = int(v.get("refs_checked") or 0)
    refs_fab = len(v.get("refs_fabricated") or [])
    tool_failures = sum(1 for e in (getattr(result, "evidence", []) or []) if not e.get("ok"))
    extras: dict[str, Any] = {}

    if task.kind == "verify":
        ok = passed and str(v.get("goal_fault") or "") == task.expect_fault
        reason = (
            f"达成且故障键正确（{v.get('goal_fault')}）"
            if ok
            else f"passed={passed}, goal_fault={v.get('goal_fault')!r}（期望 {task.expect_fault!r}）"
        )

    elif task.kind == "author":
        drafts = _outcomes(result, "draft_test_case")
        runs = _outcomes(result, "run_draft")
        drafted = any(d.get("draft_id") for d in drafts)
        all_passed = any(r.get("all_passed") is True for r in runs)
        ok = drafted and all_passed
        extras = {"drafted": drafted, "ran": bool(runs), "all_passed": all_passed}
        reason = (
            "自造用例已编译并真跑通过"
            if ok
            else f"drafted={drafted}, ran={bool(runs)}, all_passed={all_passed}"
        )

    elif task.kind == "diagnose":
        diags = _outcomes(result, "symptom_diagnose")
        matched = any(d.get("matched") is True for d in diags)
        cands = max((int(d.get("candidates") or 0) for d in diags), default=0)
        ok = matched and cands > 0
        extras = {"symptom_matched": matched, "candidates": cands}
        reason = (
            f"症状命中并给出 {cands} 个候选"
            if ok
            else f"symptom_diagnose matched={matched}, candidates={cands}"
        )

    elif task.kind == "honest_fail":
        # 负例：正确行为是"不编造结论"，**而不是"必须调用过工具"**。
        #
        # 这条判据改过一次，来历值得记：最初要求 bool(tools)，理由是"证明它真做了工作"。
        # 首轮评测里 LLM 臂对「今天天气不错」直接礼貌拒绝、零工具调用——**这是更优行为**
        # （不浪费步数、不硬凑证据），却被判为失败，导致 LLM 臂 8/11 反而不如规则臂 10/11。
        # 度量把更好的行为算成更差，那是度量的问题。改为只看"不通过 + 无幻觉引用"。
        # 空跑不会因此得分：一个永远说"不"的 Agent 会在全部正例上挂掉。
        ok = (not passed) and refs_fab == 0
        extras = {"tools_called": len(tools), "immediate_refusal": not tools}
        reason = (
            f"如实未通过且无编造引用（工具调用 {len(tools)} 次）"
            if ok
            else f"passed={passed}, 幻觉引用={refs_fab}（负例期望不通过且不编造）"
        )

    else:  # pragma: no cover - 任务集自身受 schema 约束
        ok = False
        reason = f"未知 kind: {task.kind}"

    return TaskVerdict(
        task_id=task.id,
        kind=task.kind,
        matched=ok,
        reason=reason,
        passed=passed,
        steps=int(getattr(result, "steps", 0)),
        tool_calls=len(tools),
        refs_checked=refs_checked,
        refs_fabricated=refs_fab,
        tool_failures=tool_failures,
        duration_ms=int(v.get("duration_ms") or 0),
        extras=extras,
    )


__all__ = ["DATA", "EvalTask", "TaskVerdict", "check", "load_tasks"]
