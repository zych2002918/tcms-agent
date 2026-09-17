"""P4 Agent 工作流编排 + Harness 自证。

Agent 循环（对每个任务）：
    plan      —— 解析任务 → 得到目标故障与期望处置
    retrieve  —— 从知识底座检索证据（fault 描述/相关场景/邻接）
    act       —— 选择并真实执行覆盖该故障的场景（上游 tcms 引擎）
    verify    —— 断言期望处置被实际执行满足
    reflect   —— 失败时重试其他场景/记录原因（自愈计数）
    report    —— 结构化结果 + 轨迹

后端抽象（可插拔）：
    AgentBackend.plan(task) -> dict   —— mock 返回确定性计划；LLM 后端接 key 即用
    离线优先：MockAgent 确定性、可测、可复现（Harness 红线）。

Harness 评分（结果 + 轨迹双轨）：
    pass      任务是否达成（真实执行 + 期望断言）
    evidence  agent 检索到的证据数（是否用上了知识底座）
    coverage  相关故障键覆盖
    self_heal 首轮失败后经反思达成（体现 agent 价值）
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import AssetModel
from ..knowledge import HybridRetriever
from .tasks import TaskDef

# ---------------------------------------------------------------------------
# 后端抽象
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    task_id: str
    fault: str
    expected_action: str
    chosen_scenario: str  # 覆盖该故障的场景文件
    strategy: str  # 一句话策略（LLM 后端可写自由文本）


class AgentBackend(ABC):
    """Agent 的决策后端。mock = 确定性；LLM = 自由决策。"""

    @abstractmethod
    def plan(self, task: TaskDef, evidence: list[dict], scenarios: list[dict]) -> Plan: ...


class MockAgentBackend(AgentBackend):
    """确定性后端：按故障键选第一个覆盖它的场景。离线可复现（Harness 用）。"""

    def plan(self, task: TaskDef, evidence: list[dict], scenarios: list[dict]) -> Plan:
        fault = task.target_fault
        hit = next((s for s in scenarios if fault in s.get("fault_keys", [])), None)
        if hit is None:
            raise ValueError(f"无场景覆盖故障 {fault}")
        return Plan(
            task_id=task.task_id,
            fault=fault,
            expected_action=task.expected_action,
            chosen_scenario=hit["file"],
            strategy=f"确定性：选首个覆盖 {fault} 的场景 {hit['file']}",
        )


# ---------------------------------------------------------------------------
# Harness：轨迹 + 执行 + 评分
# ---------------------------------------------------------------------------


@dataclass
class TaskRun:
    task_id: str
    fault: str
    expected_action: str
    plan: Plan | None = None
    evidence: list[dict] = field(default_factory=list)
    execution: dict | None = None
    achieved: bool = False
    attempts: int = 0
    reflected: bool = False  # 是否经反思（首轮失败后改进）
    trace: list[dict] = field(default_factory=list)  # 轨迹审计
    started: float = field(default_factory=time.time)
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)
    review: object | None = None  # RuleReviewer 的 ReviewVerdict（评审驱动自愈）

    def log(self, step: str, detail: str) -> None:
        self.trace.append({"step": step, "detail": detail, "t": round(time.time() - self.started, 3)})

    def score(self) -> dict:
        """结果 + 轨迹双轨评分（0-100）。"""
        base = 0
        if self.achieved:
            base += 60  # 任务达成是大头
        if self.evidence:
            base += min(15, len(self.evidence) * 5)  # 用了知识底座证据
        if self.execution and self.execution.get("all_passed"):
            base += 15  # 真实执行全过
        if self.reflected and self.achieved:
            base += 10  # 反思后达成（agent 价值）
        return {
            "score": min(100, base),
            "achieved": self.achieved,
            "evidence_count": len(self.evidence),
            "exec_passed": bool(self.execution and self.execution.get("all_passed")),
            "reflected": self.reflected,
            "attempts": self.attempts,
            # Q7 fresume 式四维雷达（各轴 0-100，语义直观可解释）
            "radar": {
                "goal_achieved": 100 if self.achieved else 0,  # 达成
                "evidence_used": min(100, len(self.evidence) * 25),  # 证据覆盖
                "exec_pass": 100 if (self.execution and self.execution.get("all_passed")) else 0,  # 真实执行
                "reflection": 100 if self.reflected else (50 if self.attempts > 1 else 0),  # 反思/自愈
            },
        }


class AgentHarness:
    """编排器：给定任务 → 跑完整循环 → 返回 TaskRun（含轨迹）。"""

    def __init__(
        self,
        model: AssetModel,
        retriever: HybridRetriever,
        scenario_dir: str | Path,
        backend: AgentBackend | None = None,
    ) -> None:
        self.model = model
        self.retriever = retriever
        self.scenario_dir = Path(scenario_dir)
        self.backend = backend or MockAgentBackend()

    def _scenario_index(self) -> list[dict]:
        return [
            {
                "file": s.file,
                "name": s.name,
                "fault_keys": sorted(s.fault_keys),
                "nodes": sorted(s.nodes),
            }
            for s in self.model.scenarios.values()
        ]

    def _run_scenario(self, file: str) -> dict:
        import tcms.scenarios as sc  # noqa: PLC0415

        return sc.run_yaml(str(self.scenario_dir / file))

    def run_task(self, task: TaskDef) -> TaskRun:
        run = TaskRun(task_id=task.task_id, fault=task.target_fault, expected_action=task.expected_action)
        scenarios = self._scenario_index()
        run.log("plan", f"任务: {task.title}")

        # 1. retrieve evidence
        if task.kb_query:
            try:
                r = self.retriever.retrieve(task.kb_query, k=5)
                run.evidence = r["hits"]
                # 把命中的来源 doc_id 记进轨迹（RAG 证据链对用户可见）
                srcs = ", ".join(
                    f"{h.get('doc_id')}({round(h.get('score', 0), 2)})" for h in r["hits"][:5]
                )
                run.log("retrieve", f"知识底座命中 {len(r['hits'])} 条证据：{srcs}")
            except Exception as e:  # 检索失败不阻塞（诚实记录）
                run.notes.append(f"retrieve 失败: {e}")
                run.log("retrieve", f"检索失败: {e}")

        # 2. plan
        try:
            plan = self.backend.plan(task, run.evidence, scenarios)
            run.plan = plan
            run.log("act", f"计划: {plan.strategy}")
        except Exception as e:
            run.log("act", f"计划失败: {e}")
            run.notes.append(str(e))
            run.duration_ms = int((time.time() - run.started) * 1000)
            return run

        # 3. act + verify（最多 2 次尝试，含一次反思重试）
        candidates = [s for s in scenarios if task.target_fault in s.get("fault_keys", [])]
        attempted = set()
        while run.attempts < 2 and not run.achieved:
            run.attempts += 1
            # 第 1 次用 plan 场景；反思轮换下一个候选
            if run.attempts == 1:
                file = plan.chosen_scenario
            else:
                file = next(
                    (c["file"] for c in candidates if c["file"] not in attempted and c["file"] != plan.chosen_scenario),
                    plan.chosen_scenario,
                )
            attempted.add(file)
            run.log("exec", f"真实执行场景 {file}（第 {run.attempts} 次）")
            try:
                rep = self._run_scenario(file)
                run.execution = rep
                # verify：期望处置是否在断言中通过
                asserts = rep.get("assertions", [])
                relevant = [a for a in asserts if a.get("fault") == task.target_fault]
                if relevant and all(a.get("passed") for a in relevant):
                    if any(a.get("actual") == task.expected_action for a in relevant):
                        run.achieved = True
                run.log("verify", f"相关断言 {len(relevant)} 条; achieved={run.achieved}")
            except Exception as e:
                run.notes.append(f"执行 {file} 失败: {e}")
                run.log("exec", f"执行异常: {e}")
            if not run.achieved and run.attempts == 1:
                run.reflected = True
                run.log("reflect", "首轮未达成，反思换场景重试")

        run.duration_ms = int((time.time() - run.started) * 1000)
        if run.achieved:
            run.log("report", f"达成: {task.target_fault} → {task.expected_action}")
        else:
            run.log("report", "未达成（见 notes）")

        # 4. 评审（evaluator-optimizer）→ 发现缺口则自动修正一轮（评审驱动自愈）
        from .reviewer import RuleReviewer

        reviewer = RuleReviewer(self.model, self.retriever)
        run.review = reviewer.review(task, run)
        gaps = [d for d, st in run.review.dimensions.items() if st in ("warn", "fail")]
        if gaps and not getattr(run, "_review_healed", False):
            run._review_healed = True
            run.reflected = True
            run.log(
                "reflect",
                f"评审发现缺口 ({'/'.join(gaps)})，执行修正重试…",
            )
            # 修正：换一个未试过的覆盖场景重跑，期望消除执行相关缺口
            for c in candidates:
                if c["file"] != (run.plan.chosen_scenario if run.plan else None):
                    try:
                        rep = self._run_scenario(c["file"])
                        run.execution = rep
                        run.log("exec", f"修正重试：真实执行 {c['file']}")
                        asserts = rep.get("assertions", [])
                        relevant = [a for a in asserts if a.get("fault") == task.target_fault]
                        if relevant and all(a.get("passed") for a in relevant) and any(
                            a.get("actual") == task.expected_action for a in relevant
                        ):
                            run.achieved = True
                            run.log("verify", f"修正后相关断言通过; achieved={run.achieved}")
                        break
                    except Exception as e:  # noqa: BLE001
                        run.notes.append(f"修正执行 {c['file']} 失败: {e}")
            # 复评
            run.review = reviewer.review(task, run)
            healed = [d for d, st in run.review.dimensions.items() if st in ("warn", "fail")]
            run.log("report", f"修正后复评：缺口 {len(gaps)}→{len(healed)}")
        elif run.attempts == 1 and run.achieved and not run.reflected:
            # 一次通过也要有可见的“反思环节”（自检）：评审无缺口、无需修正（诚实：未触发修正）
            run.log("reflect", "自检：6 维评审无缺口，首轮通过，无需修正")
        return run

    def run_tasks(self, tasks: list[TaskDef]) -> dict:
        runs = [self.run_task(t) for t in tasks]
        achieved = sum(1 for r in runs if r.achieved)
        # 评审在 run_task 内完成（评审驱动自愈后复评），此处汇总
        reviews = [r.review.to_dict() if r.review else {} for r in runs]
        review_ok = sum(1 for rv in reviews if rv.get("passed"))
        return {
            "total": len(runs),
            "achieved": achieved,
            "success_rate": round(achieved / len(runs), 3) if runs else 0.0,
            "review_passed": review_ok,
            "runs": [
                {
                    "task_id": r.task_id,
                    "fault": r.fault,
                    "expected": r.expected_action,
                    "achieved": r.achieved,
                    "attempts": r.attempts,
                    "reflected": r.reflected,
                    "scenario": r.plan.chosen_scenario if r.plan else None,
                    "duration_ms": r.duration_ms,
                    "score": r.score(),
                    "review": reviews[i],
                    "evidence": [
                        {
                            "doc_id": h.get("doc_id"),
                            "kind": h.get("kind"),
                            "score": h.get("score"),
                            "text": (h.get("text") or "")[:200],
                            "neighbors": [
                                {"id": nb.get("id"), "label": nb.get("label"), "kind": nb.get("kind"), "via": nb.get("via")}
                                for nb in (h.get("graph_neighbors") or [])[:4]
                            ],
                        }
                        for h in r.evidence[:5]
                    ],
                    "trace": r.trace,
                }
                for i, r in enumerate(runs)
            ],
        }
