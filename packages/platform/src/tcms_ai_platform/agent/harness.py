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
    #: 用 **perf_counter** 测量时长：既单调（不受 NTP/手动校时回拨影响），又是当前平台分辨率最高的时钟。
    #: 两个坑都踩过：time.time() 会被校时回拨；time.monotonic() 在 Windows 走 GetTickCount64、
    #: 分辨率约 15.6ms——整条链若在同一 tick 内跑完，时间戳会一起被 round(…, 3) 抹成 0.0。
    started: float = field(default_factory=time.perf_counter)
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)
    review: object | None = None  # RuleReviewer 的 ReviewVerdict（评审驱动自愈）
    #: 每条轨迹产生时的即时回调（用于把真实步骤流式推给前端做白盒剖析）
    _on_log: object = field(default=None, repr=False, compare=False)

    def log(self, step: str, detail: str, payload: dict | None = None) -> None:
        """记一条轨迹。

        `detail` 是给人看的一句话；`payload` 是**结构化白盒载荷**——
        真实查询串、候选集合、选择理由、注入了哪些故障、断言明细等。
        前端展开某一步时看的就是它，因此这里放的是"可核对的事实"，
        不是又一层转述。
        """
        entry: dict = {"step": step, "detail": detail, "t": round(time.perf_counter() - self.started, 3)}
        if payload:
            entry["payload"] = payload
        self.trace.append(entry)
        cb = self._on_log
        if callable(cb):
            try:
                cb(entry)
            except Exception:  # noqa: BLE001 - 推送失败绝不影响正跑的任务
                pass

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

    def _injected_breakdown(self, file: str) -> list[dict]:
        """该场景**实际注入了哪些故障**（白盒用：回答"为什么有三个故障"）。

        直接读场景定义，不猜：每个 inject 步骤的故障键、等级、期望处置、
        现象说明与注入时刻。用户从某个故障点进来时，最容易困惑的就是
        "我只点了一个，为什么动画里有三个"——这里给出可核对的事实依据。
        """
        from ..core.scenario_view import scenario_injections  # noqa: PLC0415

        sc_def = self.model.scenarios.get(file)
        return scenario_injections(sc_def) if sc_def is not None else []

    def run_task(self, task: TaskDef, on_log: object = None) -> TaskRun:
        run = TaskRun(task_id=task.task_id, fault=task.target_fault, expected_action=task.expected_action)
        run._on_log = on_log
        scenarios = self._scenario_index()
        run.log(
            "plan",
            f"任务: {task.title}",
            {
                "stage": "任务装载",
                "task_id": task.task_id,
                "title": task.title,
                "target_fault": task.target_fault,
                "expected_action": task.expected_action,
                "kb_query": task.kb_query or "",
                "scenario_pool": len(scenarios),
            },
        )

        # 1. retrieve evidence
        if task.kb_query:
            try:
                r = self.retriever.retrieve(task.kb_query, k=5)
                run.evidence = r["hits"]
                # 把命中的来源 doc_id 记进轨迹（RAG 证据链对用户可见）
                srcs = ", ".join(
                    f"{h.get('doc_id')}({round(h.get('score', 0), 2)})" for h in r["hits"][:5]
                )
                run.log(
                    "retrieve",
                    f"知识底座命中 {len(r['hits'])} 条证据：{srcs}",
                    {
                        "stage": "知识底座检索",
                        "query": task.kb_query,
                        "k": 5,
                        "hits": [
                            {
                                "doc_id": h.get("doc_id"),
                                "score": round(float(h.get("score", 0)), 4),
                                "sources": h.get("sources") or h.get("channels") or [],
                                "asset_ref": h.get("asset_ref") or h.get("source") or "",
                                "excerpt": (h.get("text") or "")[:180],
                            }
                            for h in r["hits"]
                        ],
                        "route_source": r.get("route_source"),
                        "domains": r.get("domains") or [],
                        "mixed_fallback": r.get("mixed_fallback", False),
                    },
                )
            except Exception as e:  # 检索失败不阻塞（诚实记录）
                run.notes.append(f"retrieve 失败: {e}")
                run.log("retrieve", f"检索失败: {e}", {"stage": "知识底座检索", "error": str(e)})

        # 2. plan
        try:
            plan = self.backend.plan(task, run.evidence, scenarios)
            run.plan = plan
            covering = [s for s in scenarios if task.target_fault in s.get("fault_keys", [])]
            run.log(
                "act",
                f"计划: {plan.strategy}",
                {
                    "stage": "决策（选场景）",
                    "backend": type(self.backend).__name__,
                    "target_fault": task.target_fault,
                    "expected_action": task.expected_action,
                    "chosen_scenario": plan.chosen_scenario,
                    "strategy": plan.strategy,
                    # 决策时的**完整候选集**：白盒的关键——不只给结果，给选择空间
                    "candidates": [
                        {
                            "file": s["file"],
                            "name": s.get("name", ""),
                            "fault_keys": s.get("fault_keys", []),
                            "is_chosen": s["file"] == plan.chosen_scenario,
                            # 该候选注入了几个故障 —— 用户最困惑的点，提前摊开
                            "inject_count": len(self._injected_breakdown(s["file"])),
                        }
                        for s in covering
                    ],
                    "evidence_used": len(run.evidence),
                },
            )
        except Exception as e:
            run.log("act", f"计划失败: {e}", {"stage": "决策（选场景）", "error": str(e)})
            run.notes.append(str(e))
            run.duration_ms = int((time.perf_counter() - run.started) * 1000)
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
            injected = self._injected_breakdown(file)
            run.log(
                "exec",
                f"真实执行场景 {file}（第 {run.attempts} 次）"
                + (f"；该场景注入 {len(injected)} 个故障" if injected else ""),
                {
                    "stage": f"真实执行（第 {run.attempts} 次）",
                    "scenario": file,
                    "scenario_name": getattr(self.model.scenarios.get(file), "name", "") or "",
                    "attempt": run.attempts,
                    # 白盒要点：**这个场景实际注入了哪些故障**，一眼看清
                    "injected": injected,
                    "injected_count": len(injected),
                },
            )
            try:
                rep = self._run_scenario(file)
                run.execution = rep
                # verify：期望处置是否在断言中通过
                asserts = rep.get("assertions", [])
                relevant = [a for a in asserts if a.get("fault") == task.target_fault]
                if relevant and all(a.get("passed") for a in relevant):
                    if any(a.get("actual") == task.expected_action for a in relevant):
                        run.achieved = True
                run.log(
                    "verify",
                    f"相关断言 {len(relevant)} 条; achieved={run.achieved}",
                    {
                        "stage": "断言核对",
                        "target_fault": task.target_fault,
                        "expected_action": task.expected_action,
                        "assertions_total": len(asserts),
                        "relevant": [
                            {
                                "fault": a.get("fault"),
                                "expect": a.get("expect") or a.get("expected"),
                                "actual": a.get("actual"),
                                "passed": a.get("passed"),
                                "detail": a.get("detail") or a.get("note") or "",
                            }
                            for a in relevant
                        ],
                        "achieved": run.achieved,
                        "duration_ms": rep.get("duration_ms"),
                    },
                )
            except Exception as e:
                run.notes.append(f"执行 {file} 失败: {e}")
                run.log("exec", f"执行异常: {e}", {"stage": f"真实执行（第 {run.attempts} 次）", "scenario": file, "error": str(e)})
            if not run.achieved and run.attempts == 1:
                run.reflected = True
                run.log("reflect", "首轮未达成，反思换场景重试")

        run.duration_ms = int((time.perf_counter() - run.started) * 1000)
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

    @staticmethod
    def serialize_run(run: TaskRun) -> dict:
        """把一次运行序列化成与 `/api/agent/run` 的 `runs[]` **同构**的字典。

        抽出来是因为流式端点也要给出同一份结果：否则前端要么把任务跑两遍
        （浪费一次真实引擎执行与一次模型调用），要么流和报告两套结构对不上。
        """
        review = run.review.to_dict() if run.review else {}
        return {
            "task_id": run.task_id,
            "fault": run.fault,
            "expected": run.expected_action,
            "achieved": run.achieved,
            "attempts": run.attempts,
            "reflected": run.reflected,
            "scenario": run.plan.chosen_scenario if run.plan else None,
            "duration_ms": run.duration_ms,
            "score": run.score(),
            "review": review,
            "evidence": [
                {
                    "doc_id": h.get("doc_id"),
                    "kind": h.get("kind"),
                    "score": h.get("score"),
                    "text": (h.get("text") or "")[:200],
                    "neighbors": [
                        {
                            "id": nb.get("id"),
                            "label": nb.get("label"),
                            "kind": nb.get("kind"),
                            "via": nb.get("via"),
                        }
                        for nb in (h.get("graph_neighbors") or [])[:4]
                    ],
                }
                for h in run.evidence[:5]
            ],
            "trace": run.trace,
        }

    def summarize_runs(self, runs: list[TaskRun]) -> dict:
        """汇总多次运行（与 `/api/agent/run` 响应同构）。"""
        achieved = sum(1 for r in runs if r.achieved)
        reviews = [r.review.to_dict() if r.review else {} for r in runs]
        return {
            "total": len(runs),
            "achieved": achieved,
            "success_rate": round(achieved / len(runs), 3) if runs else 0.0,
            "review_passed": sum(1 for rv in reviews if rv.get("passed")),
            "runs": [self.serialize_run(r) for r in runs],
        }

    def run_tasks(self, tasks: list[TaskDef]) -> dict:
        return self.summarize_runs([self.run_task(t) for t in tasks])
