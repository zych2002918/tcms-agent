"""评测执行器：把任务集在多个"臂"上跑一遍，产出可比对的报告。

## 什么是一个"臂"

一个臂 = 一组配置覆盖。R7 用四个臂来回答四类此前只能"声称"的问题：

    rule            基线：离线规则臂（无 key，可复现）
    rule-no-memory  关掉记忆召回 → 记忆到底有没有用
    rule-no-rerank  关掉特征重排 → 重排到底有没有用
    llm             真模型（仅在有 key 时可用）

**每臂独立记忆目录**：否则后跑的臂会读到前一个臂写下的运行日志，
臂与臂之间不再是独立对照。这是对照实验的基本要求。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..knowledge import KnowledgeContext, build_knowledge
from ..models import llm_available
from ..runner import AgentRunner
from .metrics import EvalReport, compute_metrics
from .tasks import EvalTask, check, load_tasks


@dataclass
class Arm:
    name: str
    overrides: dict[str, Any]
    requires_llm: bool = False
    engine: str = "graph"
    """编排引擎：`graph`（LangGraph）或 `nolib`（手写最小 loop）。

    同一套任务、同一批节点、同一套工具，只有编排层不同 —— 这样才能隔离出
    "框架到底提供了什么"。"""

    def available(self) -> tuple[bool, str]:
        if self.requires_llm and not llm_available():
            return False, "未配置 LLM key"
        return True, ""


def default_arms() -> list[Arm]:
    """默认的对照臂。

    **为什么记忆的对照要放在 LLM 臂上**：离线规则臂的技能是**写死的脚本**，
    无论有没有记忆都走同一条路——它结构上不可能因记忆而改变行为。
    首轮评测证实了这点：`rule` 与 `rule-no-memory` 六项指标完全相同。
    所以"记忆有没有用"只能在 LLM 臂上度量（那里模型才真的会参考历史做选择）。
    """
    return [
        Arm("rule", {"offline": True}),
        Arm("rule-no-rerank", {"offline": True, "rerank_enabled": False}),
        Arm("nolib", {"offline": True}, engine="nolib"),
        Arm("llm", {"offline": False}, requires_llm=True),
        Arm("llm-no-memory", {"offline": False, "memory_enabled": False}, requires_llm=True),
    ]


def run_arm(
    arm: Arm,
    tasks: list[EvalTask] | None = None,
    *,
    base_cfg: AgentConfig | None = None,
    knowledge: KnowledgeContext | None = None,
    workdir: Path | None = None,
    rounds: int = 1,
) -> EvalReport:
    """在单个臂上跑完整任务集。

    `rounds > 1`：把任务集重复跑 N 轮（同一臂内共享记忆目录）。
    **这是度量记忆的必要条件**——第一轮结束时记忆才有内容，第二轮才可能被用上；
    单轮评测里"有记忆"与"无记忆"必然完全一样（首轮评测已证实）。
    指标取**最后一轮**（此时记忆已充分积累），并给出逐轮达标率供对照。
    """
    tasks = tasks if tasks is not None else load_tasks()
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix=f"tcms-eval-{arm.name}-"))
    root.mkdir(parents=True, exist_ok=True)

    cfg = (base_cfg or AgentConfig()).with_(
        memory_dir=root / "memory",
        sandbox_dir=root / "sandbox",
        artifacts_dir=root / "artifacts",
        db_path=root / "checkpoints.sqlite",
        **arm.overrides,
    )
    ctx = knowledge or build_knowledge(cfg.upstream)
    if arm.engine == "nolib":
        from ..nolib import run_goal

        def _run_one(t: EvalTask, rnd: int):
            return run_goal(t.goal, cfg=cfg, knowledge=ctx, run_id=f"{arm.name}-r{rnd}-{t.id}")
    else:
        runner = AgentRunner(cfg, ctx)

        def _run_one(t: EvalTask, rnd: int):
            return runner.run(t.goal, thread_id=f"{arm.name}-r{rnd}-{t.id}")

    from .tasks import TaskVerdict

    verdicts: list[TaskVerdict] = []
    model_kind = ""
    per_round: list[float] = []
    for rnd in range(1, max(1, rounds) + 1):
        verdicts = []
        for t in tasks:
            t0 = time.perf_counter()
            try:
                res = _run_one(t, rnd)
                model_kind = res.model_kind
                res.verdict["duration_ms"] = int((time.perf_counter() - t0) * 1000)
                verdicts.append(check(t, res))
            except Exception as e:  # noqa: BLE001 - 单条任务崩溃不应毁掉整轮评测
                verdicts.append(
                    TaskVerdict(
                        task_id=t.id,
                        kind=t.kind,
                        matched=False,
                        reason=f"运行异常: {type(e).__name__}: {e}",
                        passed=False,
                        steps=0,
                        tool_calls=0,
                        refs_checked=0,
                        refs_fabricated=0,
                        tool_failures=0,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                    )
                )
        per_round.append(compute_metrics(verdicts)["match_rate"])

    metrics = compute_metrics(verdicts)
    metrics["rounds"] = max(1, rounds)
    metrics["per_round_match_rate"] = per_round
    return EvalReport(arm=arm.name, model_kind=model_kind, metrics=metrics, verdicts=verdicts)


def run_arms(
    arms: list[Arm] | None = None,
    tasks: list[EvalTask] | None = None,
    *,
    base_cfg: AgentConfig | None = None,
    knowledge: KnowledgeContext | None = None,
    workdir: Path | None = None,
    keep: bool = False,
) -> tuple[dict[str, EvalReport], list[str]]:
    """跑多个臂；返回 (报告字典, 跳过的臂及原因)。

    知识底座只构建一次并复用（构建约 1s，重复构建纯属浪费），
    但**记忆目录每臂独立**——见模块文档。
    """
    arms = arms if arms is not None else default_arms()
    tasks = tasks if tasks is not None else load_tasks()
    ctx = knowledge or build_knowledge(base_cfg.upstream if base_cfg else None)

    reports: dict[str, EvalReport] = {}
    skipped: list[str] = []
    for arm in arms:
        ok, why = arm.available()
        if not ok:
            skipped.append(f"{arm.name}（{why}）")
            continue
        arm_dir = Path(workdir) / arm.name if workdir else None
        reports[arm.name] = run_arm(arm, tasks, base_cfg=base_cfg, knowledge=ctx, workdir=arm_dir)
    if workdir and not keep:
        shutil.rmtree(workdir, ignore_errors=True)
    return reports, skipped


def render_reports(reports: dict[str, EvalReport], skipped: list[str] | None = None) -> str:
    """把多臂报告渲染成对比表（人读）。"""
    if not reports:
        return "（没有可运行的臂）"
    names = list(reports)
    lines = ["评测结果（各臂任务集相同；记忆目录各自独立）", ""]
    for name in names:
        lines += reports[name].summary_lines()
        lines.append("")
    metrics = [
        "match_rate",
        "avg_steps",
        "avg_tool_calls",
        "tool_failure_rate",
        "hallucination_rate",
        "self_healed",
    ]
    head = "指标".ljust(20) + "".join(n.ljust(18) for n in names)
    lines += [head, "-" * len(head)]
    for m in metrics:
        row = m.ljust(20)
        for n in names:
            v = reports[n].metrics.get(m, "—")
            row += (f"{v:.4f}" if isinstance(v, float) else str(v)).ljust(18)
        lines.append(row)
    if skipped:
        lines += ["", f"跳过的臂：{'；'.join(skipped)}"]
    return "\n".join(lines)


__all__ = ["Arm", "default_arms", "render_reports", "run_arm", "run_arms"]
