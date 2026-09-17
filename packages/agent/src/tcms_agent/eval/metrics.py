"""评测指标与 A/B 门禁。

## 指标设计的三条纪律

1. **口径写在这里，不在报告里**：任何对外引用的数字都必须能由 `compute_metrics` 复现。
2. **分母诚实**：比率类指标一律给出分子/分母，绝不只给一个百分数
   （原三仓的 metrics.md 就强调"分母含残缺样本"）。
3. **负例计入总分**：`match_rate` 的分母是**全部任务**，包含"应当诚实失败"的负例——
   否则一个"什么都说好"的 Agent 能刷分。

## 门禁为什么用容差而不是"必须不劣化"

LLM 臂本身有随机性，严格相等会天天误报。门禁比的是**关键指标是否跌破容差**，
并单独把"回退的任务"列出来供人看——数字与个案都要给。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from .tasks import TaskVerdict


@dataclass
class EvalReport:
    """一次评测（一个"臂"）的完整结果。"""

    arm: str
    model_kind: str
    metrics: dict[str, Any]
    verdicts: list[TaskVerdict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "model_kind": self.model_kind,
            "metrics": self.metrics,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }

    @property
    def match_rate(self) -> float:
        return float(self.metrics.get("match_rate") or 0.0)

    def summary_lines(self) -> list[str]:
        m = self.metrics
        return [
            f"[{self.arm}] model={self.model_kind}",
            f"  任务达成 {m['matched']}/{m['tasks']}（{m['match_rate']:.0%}）"
            + (
                f" · 逐轮达标 {m['per_round_match_rate']}"
                if m.get("rounds", 1) > 1
                else ""
            ),
            f"  平均步数 {m['avg_steps']} · 平均工具调用 {m['avg_tool_calls']} · "
            f"工具失败率 {m['tool_failure_rate']:.0%}",
            f"  引用校验 {m['refs_checked']} 条 · 幻觉引用 {m['refs_fabricated']} 条"
            f"（幻觉率 {m['hallucination_rate']:.0%}）",
            f"  自愈（首轮有工具失败但任务仍达成）{m['self_healed']} 条",
        ]


def compute_metrics(verdicts: list[TaskVerdict]) -> dict[str, Any]:
    """从逐任务判定汇总指标（分子/分母一并给出）。"""
    n = len(verdicts)
    if n == 0:
        return {"tasks": 0, "matched": 0, "match_rate": 0.0}
    matched = sum(1 for v in verdicts if v.matched)
    refs = sum(v.refs_checked for v in verdicts)
    fab = sum(v.refs_fabricated for v in verdicts)
    calls = sum(v.tool_calls for v in verdicts)
    fails = sum(v.tool_failures for v in verdicts)
    # 自愈：该任务中出现过工具失败，但最终仍然达成
    healed = sum(1 for v in verdicts if v.tool_failures > 0 and v.matched)
    by_kind: dict[str, dict[str, int]] = {}
    for v in verdicts:
        b = by_kind.setdefault(v.kind, {"tasks": 0, "matched": 0})
        b["tasks"] += 1
        b["matched"] += int(v.matched)
    return {
        "tasks": n,
        "matched": matched,
        "match_rate": round(matched / n, 4),
        "by_kind": by_kind,
        "avg_steps": round(mean(v.steps for v in verdicts), 2),
        "avg_tool_calls": round(mean(v.tool_calls for v in verdicts), 2),
        "tool_calls_total": calls,
        "tool_failures_total": fails,
        "tool_failure_rate": round(fails / calls, 4) if calls else 0.0,
        "refs_checked": refs,
        "refs_fabricated": fab,
        "hallucination_rate": round(fab / refs, 4) if refs else 0.0,
        "self_healed": healed,
    }


# ---------------------------------------------------------------------------
# A/B 门禁
# ---------------------------------------------------------------------------

#: 门禁关注的关键指标与容差（跌破即判回退）。
#: 容差不是"放水"：LLM 臂有随机性，严格相等会天天误报；个案回退另行列出。
GATE_METRICS: dict[str, tuple[str, float]] = {
    # 指标: (方向 higher_is_better 用 "+" / lower_is_better 用 "-", 容差)
    "match_rate": ("+", 0.0),
    "hallucination_rate": ("-", 0.0),
    "tool_failure_rate": ("-", 0.25),
    "avg_tool_calls": ("-", 2.0),
    "avg_steps": ("-", 3.0),
}


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    regressed_tasks: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "regressed_tasks": self.regressed_tasks,
            "improvements": self.improvements,
        }

    def summary_lines(self) -> list[str]:
        head = "✅ 门禁通过" if self.passed else "❌ 门禁未通过"
        lines = [head]
        for r in self.reasons:
            lines.append(f"  - {r}")
        if self.regressed_tasks:
            lines.append(f"  回退任务：{'、'.join(self.regressed_tasks)}")
        if self.improvements:
            lines.append(f"  改进任务：{'、'.join(self.improvements)}")
        return lines


def compare(
    candidate: EvalReport, baseline: EvalReport, *, tolerances: dict[str, float] | None = None
) -> GateResult:
    """候选臂 vs 基线臂：关键指标是否跌破容差 + 逐任务回退。"""
    tol = {**{k: v[1] for k, v in GATE_METRICS.items()}, **(tolerances or {})}
    reasons: list[str] = []
    for metric, (direction, _default) in GATE_METRICS.items():
        c = float(candidate.metrics.get(metric) or 0.0)
        b = float(baseline.metrics.get(metric) or 0.0)
        slack = tol.get(metric, 0.0)
        if direction == "+":
            if c < b - slack:
                reasons.append(f"{metric} 跌破：{b:.4f} → {c:.4f}（容差 {slack}）")
        else:
            if c > b + slack:
                reasons.append(f"{metric} 上升：{b:.4f} → {c:.4f}（容差 {slack}）")

    base_map = {v.task_id: v.matched for v in baseline.verdicts}
    cand_map = {v.task_id: v.matched for v in candidate.verdicts}
    regressed = sorted(t for t, ok in base_map.items() if ok and not cand_map.get(t, False))
    improved = sorted(t for t, ok in cand_map.items() if ok and not base_map.get(t, False))
    if regressed:
        reasons.append(f"{len(regressed)} 条任务从达成变为未达成")
    return GateResult(
        passed=not reasons, reasons=reasons, regressed_tasks=regressed, improvements=improved
    )


__all__ = ["GATE_METRICS", "EvalReport", "GateResult", "compare", "compute_metrics"]
