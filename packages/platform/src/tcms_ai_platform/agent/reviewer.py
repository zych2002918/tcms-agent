"""评审 Agent（Reviewer）：对一次 Agent run 做多视角真实规则评审。

参考 Anthropic「evaluator-optimizer」：生成/执行之后，用另一套视角评审，
产出可行动的反馈。评审基于**真实领域知识**（扩充后的图谱 + 资产），
不是 LLM 自评——每个评审维度都可追溯到 KB 里的真实实体：

视角（review dimensions）：
1. result_grounded   —— 达成是否由真实引擎断言支撑（不是空跑）
2. evidence_used     —— 是否检索到了知识底座证据（用了扩充后的领域知识？）
3. threshold_aware   —— 任务目标是否触及真实安全阈值（KB 中 threshold 节点）
4. interlock_aware   —— 是否涉及联锁规则（KB 中 interlock 节点）
5. requirement_trace —— 是否可追溯到安全需求（SR）
6. honesty           —— 失败是否如实记录（不粉饰）

每维给 pass/warn/fail + 理由，最终产出 review {dimensions, issues, verdict}，
可被 TaskRun 吸收为额外评分信号并展示给用户（人工可介入复核）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import AssetModel
from ..knowledge import HybridRetriever
from .tasks import TaskDef

# 任务 → 应触及的真实领域知识（key 词，用于在 KB 检索确认）
TASK_KB_ANCHORS: dict[str, dict] = {
    "T-EBM": {
        "thresholds": ["160", "300", "0.5"],
        "keywords": ["emergency_brake", "紧急制动", "exec_feedback", "三重证据"],
        "sr": ["SR-01", "SR-02", "SR-03", "SR-13"],
    },
    "T-DOOR": {
        "thresholds": ["0.5"],
        "keywords": ["door_fault", "车门", "联锁"],
        "sr": ["SR-04"],
    },
    "T-OVERSPEED": {
        "thresholds": ["160"],
        "keywords": ["超速", "ATP", "EBI"],
        "sr": ["SR-05", "SR-06"],
    },
    "T-CONFLICT": {
        "thresholds": [],
        "keywords": ["牵引", "制动", "冲突", "联锁"],
        "sr": ["SR-14"],
    },
    "T-HEARTBEAT": {
        "thresholds": ["100"],
        "keywords": ["心跳", "看门狗", "watchdogs", "离线"],
        "sr": ["SR-07"],
    },
    "T-BUS": {
        "thresholds": ["256", "128"],
        "keywords": ["总线", "短路", "Bus-Off", "errstate", "错误状态机"],
        "sr": ["SR-08"],
    },
    "T-CRC": {
        "thresholds": [],
        "keywords": ["CRC", "crc", "校验", "错误帧", "完整性"],
        "sr": ["SR-08", "SR-16"],
    },
    "T-STORM": {
        "thresholds": ["100"],
        "keywords": ["重启", "健康表", "心跳", "watchdogs"],
        "sr": ["SR-07", "SR-11"],
    },
}


@dataclass
class ReviewVerdict:
    """一次 run 的评审结论。"""

    task_id: str
    dimensions: dict[str, str] = field(default_factory=dict)  # name -> pass/warn/fail
    issues: list[str] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "dimensions": self.dimensions,
            "issues": self.issues,
            "passed": self.passed,
        }


class RuleReviewer:
    """基于真实领域知识的规则评审器（确定性、可复现）。"""

    def __init__(self, model: AssetModel, retriever: HybridRetriever) -> None:
        self.model = model
        self.retriever = retriever

    def _kb_has(self, kind: str, keyword: str) -> bool:
        """在图谱中查找某类节点是否含关键词（宽松匹配 label/props）。"""
        for n in self.retriever.graph.nodes.values():
            if n.kind != kind:
                continue
            hay = (n.label + " " + str(n.props)).lower()
            if keyword.lower() in hay:
                return True
        return False

    def review(self, task: TaskDef, run) -> ReviewVerdict:
        """评审一次 TaskRun（run 需含 achieved/evidence/execution/trace）。"""
        v = ReviewVerdict(task_id=task.task_id)
        anchors = TASK_KB_ANCHORS.get(task.task_id, {})
        sr_list = anchors.get("sr", [])
        kw = anchors.get("keywords", [])
        th = anchors.get("thresholds", [])

        # 1. 结果真实性（真实引擎断言支撑）
        if run.execution and run.execution.get("assertions"):
            v.dimensions["result_grounded"] = "pass"
        elif run.achieved:
            v.dimensions["result_grounded"] = "warn"
            v.issues.append("达成但未见真实断言细节（可能缺执行证据）")
        else:
            v.dimensions["result_grounded"] = "fail"
            v.issues.append("任务未达成——真实执行失败")

        # 2. 证据使用
        if run.evidence:
            v.dimensions["evidence_used"] = "pass"
        else:
            v.dimensions["evidence_used"] = "warn"
            v.issues.append("未使用知识底座证据（检索失败或跳过）")

        # 3. 阈值感知（真实安全阈值是否在 KB 中可关联）
        if th:
            hit = [t for t in th if self._kb_has("threshold", t) or self._kb_has("threshold", f"{t} ")]
            if hit:
                v.dimensions["threshold_aware"] = "pass"
            else:
                v.dimensions["threshold_aware"] = "warn"
                v.issues.append(f"任务涉及阈值 {th} 但 KB 未见对应 threshold 节点")
        else:
            v.dimensions["threshold_aware"] = "pass"  # 无阈值要求视为通过

        # 4. 联锁/机制意识（关键词是否命中 KB 联锁/机制/概念）
        if kw:
            kb_hits = 0
            for k in kw:
                if any(self._kb_has(kind, k) for kind in ("interlock", "mechanism", "concept", "fault")):
                    kb_hits += 1
            if kb_hits >= max(1, len(kw) // 2):
                v.dimensions["domain_aware"] = "pass"
            else:
                v.dimensions["domain_aware"] = "warn"
                v.issues.append("领域知识命中偏少——可能没触及真实联锁/机制语义")
        else:
            v.dimensions["domain_aware"] = "pass"

        # 5. 需求追溯
        if sr_list:
            missing = [s for s in sr_list if f"requirement:{s}" not in self.retriever.graph.nodes]
            if not missing:
                v.dimensions["requirement_trace"] = "pass"
            else:
                v.dimensions["requirement_trace"] = "warn"
                v.issues.append(f"任务应追溯需求 {missing} 但图谱无对应节点")
        else:
            v.dimensions["requirement_trace"] = "pass"

        # 6. 诚实性：失败时 trace 应含 reflect/notes
        if not run.achieved:
            steps = [t.get("step") for t in run.trace]
            if "reflect" in steps or run.notes:
                v.dimensions["honesty"] = "pass"
            else:
                v.dimensions["honesty"] = "fail"
                v.issues.append("失败但无反思/说明——记录不诚实")
        else:
            v.dimensions["honesty"] = "pass"

        # 汇总
        fails = [d for d, st in v.dimensions.items() if st == "fail"]
        v.passed = not fails
        if fails:
            v.issues.insert(0, f"评审未过：{len(fails)} 个维度失败（{'/'.join(fails)}）")
        return v

    def review_runs(self, tasks: list[TaskDef], runs: list) -> list[dict]:
        """批量评审（与 run_tasks 的 runs 对齐）。"""
        out = []
        for t, r in zip(tasks, runs):
            v = self.review(t, r)
            out.append(v.to_dict())
        return out
