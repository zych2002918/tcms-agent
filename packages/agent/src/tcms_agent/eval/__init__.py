"""评测体系（R7）：任务集 + 指标 + A/B 门禁。

回答一个此前只能"声称"的问题：**这个 Agent 到底能不能干活，以及每个设计选择有没有用。**

    tasks.py    任务集与机器判定（含负例：应当诚实失败的目标）
    metrics.py  指标口径（分子/分母一并给出）与 A/B 门禁
    harness.py  在多个"臂"上跑同一套任务，产出可比对报告

纪律：
- 任务集期望值全部锚定真实资产，且必须能在**无 key** 的离线臂下判定 → 评测可进 CI；
- 负例与正例同权：`match_rate` 的分母是全部任务，**"什么都说好"的 Agent 会被扣分**；
- 对照实验每臂独立记忆目录，否则臂之间不再独立。
"""

from .harness import Arm, default_arms, render_reports, run_arm, run_arms
from .metrics import EvalReport, GateResult, compare, compute_metrics
from .tasks import EvalTask, TaskVerdict, check, load_tasks

__all__ = [
    "Arm",
    "EvalReport",
    "EvalTask",
    "GateResult",
    "TaskVerdict",
    "check",
    "compare",
    "compute_metrics",
    "default_arms",
    "load_tasks",
    "render_reports",
    "run_arm",
    "run_arms",
]
