"""评测体系：任务集判定 / 指标口径 / A-B 门禁。

这套东西的意义不在于"跑个分"，而在于**让此前所有只能声称的设计选择被数据检验**：
记忆有没有用、重排有没有用、规则臂与 LLM 臂差在哪。首轮评测就抓出了两个真问题
（引用校验的假阳性、解析器的措辞依赖），见 tasks.yaml 的注释与 ADR-015。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcms_agent.config import AgentConfig
from tcms_agent.eval import (
    Arm,
    EvalReport,
    EvalTask,
    check,
    compare,
    compute_metrics,
    default_arms,
    load_tasks,
    run_arm,
)
from tcms_agent.eval.tasks import TaskVerdict
from tcms_agent.knowledge import KnowledgeContext
from tcms_agent.runner import AgentRunner


def _verdict(**kw) -> TaskVerdict:
    base = {
        "task_id": "T",
        "kind": "verify",
        "matched": True,
        "reason": "",
        "passed": True,
        "steps": 4,
        "tool_calls": 3,
        "refs_checked": 5,
        "refs_fabricated": 0,
        "tool_failures": 0,
        "duration_ms": 100,
    }
    base.update(kw)
    return TaskVerdict(**base)


# ---------------------------------------------------------------------------
# 任务集本身
# ---------------------------------------------------------------------------


def test_task_set_loads_and_is_well_formed() -> None:
    tasks = load_tasks()
    assert len(tasks) >= 8, "任务集太小无法说明问题"
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids)), "任务 id 必须唯一"
    kinds = {t.kind for t in tasks}
    assert kinds <= {"verify", "author", "diagnose", "honest_fail"}, f"未知 kind: {kinds}"
    assert "honest_fail" in kinds, "必须含负例，否则'什么都说好'的 Agent 能刷分"
    for t in tasks:
        if t.kind == "verify":
            assert t.expect_fault, f"{t.id} 是 verify 类，必须给出 expect_fault"


def test_task_set_expectations_reference_real_assets(knowledge: KnowledgeContext) -> None:
    """期望值必须锚定真实资产，不许手写。"""
    for t in load_tasks():
        if t.expect_fault:
            assert t.expect_fault in knowledge.model.faults_by_key, (
                f"{t.id} 期望的故障键不存在于 faults.yaml: {t.expect_fault}"
            )


def test_negative_tasks_exist_and_are_off_topic() -> None:
    negs = [t for t in load_tasks() if t.kind == "honest_fail"]
    assert len(negs) >= 2
    for t in negs:
        assert t.goal.strip()


# ---------------------------------------------------------------------------
# 判定逻辑
# ---------------------------------------------------------------------------


class _Res:
    """假的运行结果（只带判定需要的字段）。"""

    def __init__(self, verdict: dict, evidence: list[dict] | None = None, steps: int = 3):
        self.verdict = verdict
        self.evidence = evidence or []
        self.steps = steps


def test_check_verify_requires_correct_fault() -> None:
    t = EvalTask(id="T1", kind="verify", goal="g", expect_fault="door_fault")
    assert check(t, _Res({"passed": True, "goal_fault": "door_fault"})).matched is True
    # 通过了但故障键不对 → 不算达成（防止"碰巧通过"）
    assert check(t, _Res({"passed": True, "goal_fault": "overspeed"})).matched is False
    assert check(t, _Res({"passed": False, "goal_fault": "door_fault"})).matched is False


def test_check_author_requires_self_written_test_to_really_pass() -> None:
    t = EvalTask(id="T2", kind="author", goal="g")
    ok_ev = [
        {"tool": "draft_test_case", "ok": True, "outcome": {"draft_id": "d1", "compiled": True}},
        {"tool": "run_draft", "ok": True, "outcome": {"all_passed": True, "passed": 1, "failed": 0}},
    ]
    assert check(t, _Res({"passed": True}, ok_ev)).matched is True

    # 工具没报错，但用例断言失败 → 不算达成（ok=True 会被误读）
    bad_ev = [
        {"tool": "draft_test_case", "ok": True, "outcome": {"draft_id": "d1"}},
        {"tool": "run_draft", "ok": True, "outcome": {"all_passed": False, "failed": 1}},
    ]
    v = check(t, _Res({"passed": True}, bad_ev))
    assert v.matched is False
    assert v.extras["all_passed"] is False

    # 只造了没跑 → 不算达成
    assert check(t, _Res({"passed": True}, ok_ev[:1])).matched is False


def test_check_diagnose_requires_match_and_candidates() -> None:
    t = EvalTask(id="T3", kind="diagnose", goal="g")
    ev = [{"tool": "symptom_diagnose", "ok": True, "outcome": {"matched": True, "candidates": 4}}]
    assert check(t, _Res({}, ev)).matched is True
    # 诚实 no_match 不算达成（这类任务就是要它命中）
    nm = [{"tool": "symptom_diagnose", "ok": True, "outcome": {"matched": False, "candidates": 0}}]
    assert check(t, _Res({}, nm)).matched is False


def test_check_honest_fail_rewards_refusal_but_not_fabrication() -> None:
    """负例的正确行为是"不编造"，**不是"必须调用过工具"**。

    这条判据改过一次：最初要求 `bool(tools)`（想证明它真做了工作）。首轮评测里
    LLM 臂对「今天天气不错」直接礼貌拒绝、零工具调用——那是**更优行为**（不浪费步数、
    不硬凑证据），却被判失败，导致 LLM 臂 8/11 反而不如规则臂 10/11。
    把更好的行为算成更差，是度量的问题，所以改成只看"不通过 + 无幻觉引用"。
    """
    t = EvalTask(id="T4", kind="honest_fail", goal="今天天气不错")
    # 拒绝（无论是否调用过工具）都算对
    assert check(t, _Res({"passed": False}, [{"tool": "kb_search", "ok": True}])).matched is True
    assert check(t, _Res({"passed": False}, [])).matched is True, "立即拒绝是更优行为"
    assert check(t, _Res({"passed": False}, [])).extras["immediate_refusal"] is True
    # 硬猜出结论 → 不对
    assert check(t, _Res({"passed": True}, [{"tool": "kb_search", "ok": True}])).matched is False
    # 编造引用 → 不对
    assert check(t, _Res({"passed": False, "refs_fabricated": ["fault:ghost"]}, [])).matched is False


def test_always_say_no_agent_cannot_farm_negative_tasks() -> None:
    """防刷分：一个"永远说不"的 Agent 在负例上全对，但会在正例上全挂。

    这条测试确认负例判据放宽后**没有引入刷分漏洞**。
    """
    negs = [t for t in load_tasks() if t.kind == "honest_fail"]
    pos = [t for t in load_tasks() if t.kind != "honest_fail"]
    refuse = _Res({"passed": False}, [])
    assert all(check(t, refuse).matched for t in negs), "负例应全过"
    assert not any(check(t, refuse).matched for t in pos), "正例必须全挂，否则判据可被刷分"


# ---------------------------------------------------------------------------
# 指标口径
# ---------------------------------------------------------------------------


def test_metrics_give_numerator_and_denominator() -> None:
    m = compute_metrics([_verdict(matched=True), _verdict(matched=False, refs_fabricated=1)])
    assert m["tasks"] == 2 and m["matched"] == 1 and m["match_rate"] == 0.5
    assert m["refs_checked"] == 10 and m["refs_fabricated"] == 1
    assert m["hallucination_rate"] == pytest.approx(0.1)


def test_metrics_group_by_kind_including_negatives() -> None:
    vs = [_verdict(kind="verify"), _verdict(kind="honest_fail", matched=False)]
    m = compute_metrics(vs)
    assert m["by_kind"]["verify"] == {"tasks": 1, "matched": 1}
    assert m["by_kind"]["honest_fail"] == {"tasks": 1, "matched": 0}
    assert m["match_rate"] == 0.5, "负例必须计入分母"


def test_metrics_self_heal_counts_recovered_runs() -> None:
    vs = [
        _verdict(tool_failures=1, matched=True),  # 有失败但最终达成 → 自愈
        _verdict(tool_failures=2, matched=False),
        _verdict(tool_failures=0, matched=True),
    ]
    assert compute_metrics(vs)["self_healed"] == 1


def test_metrics_empty_is_safe() -> None:
    assert compute_metrics([])["tasks"] == 0


# ---------------------------------------------------------------------------
# 门禁
# ---------------------------------------------------------------------------


def _report(name: str, metrics: dict, matched: dict[str, bool]) -> EvalReport:
    return EvalReport(
        arm=name,
        model_kind="x",
        metrics=metrics,
        verdicts=[
            _verdict(task_id=tid, matched=ok, kind="verify") for tid, ok in matched.items()
        ],
    )


def test_gate_passes_on_identical_reports() -> None:
    m = compute_metrics([_verdict()])
    r = _report("a", m, {"T1": True})
    assert compare(r, r).passed is True


def test_gate_fails_on_match_rate_regression() -> None:
    good = _report("base", {"match_rate": 1.0, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True, "T2": True})
    bad = _report("cand", {"match_rate": 0.5, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                           "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True, "T2": False})
    g = compare(bad, good)
    assert g.passed is False
    assert any("match_rate" in r for r in g.reasons)
    assert g.regressed_tasks == ["T2"]


def test_gate_fails_on_rising_hallucination() -> None:
    base = _report("base", {"match_rate": 1.0, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True})
    cand = _report("cand", {"match_rate": 1.0, "hallucination_rate": 0.2, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True})
    g = compare(cand, base)
    assert g.passed is False
    assert any("hallucination_rate" in r for r in g.reasons)


def test_gate_tolerates_small_cost_increase() -> None:
    """成本类指标给容差：LLM 臂有随机性，严格相等会天天误报。"""
    base = _report("base", {"match_rate": 1.0, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True})
    cand = _report("cand", {"match_rate": 1.0, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 4.5, "avg_steps": 6.0}, {"T1": True})
    assert compare(cand, base).passed is True, "容差内的成本上升不应判回退"


def test_gate_reports_improvements() -> None:
    base = _report("base", {"match_rate": 0.5, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True, "T2": False})
    cand = _report("cand", {"match_rate": 1.0, "hallucination_rate": 0.0, "tool_failure_rate": 0.0,
                            "avg_tool_calls": 3.0, "avg_steps": 4.0}, {"T1": True, "T2": True})
    g = compare(cand, base)
    assert g.passed is True
    assert g.improvements == ["T2"]


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------


def test_default_arms_cover_the_key_questions() -> None:
    """记忆的对照必须落在 **LLM 臂**上。

    规则臂的技能是写死的脚本，无论有没有记忆都走同一条路——结构上不可能因记忆改变
    行为。首轮评测证实：`rule` 与 `rule-no-memory` 六项指标完全相同。
    """
    names = {a.name for a in default_arms()}
    assert {"rule", "rule-no-rerank", "llm", "llm-no-memory"} <= names
    assert "rule-no-memory" not in names, "规则臂上做记忆对照没有意义（脚本是写死的）"


def test_run_arm_supports_multiple_rounds(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """多轮是度量记忆的必要条件：第一轮结束记忆才有内容。"""
    tasks = [EvalTask(id="T1", kind="verify", goal="验证车门故障必须触发降级处置",
                      expect_fault="door_fault")]
    rep = run_arm(Arm("rule", {"offline": True}), tasks, knowledge=knowledge,
                  workdir=tmp_path / "r2", rounds=2)
    assert rep.metrics["rounds"] == 2
    assert len(rep.metrics["per_round_match_rate"]) == 2
    # 两轮都跑过 → 日志里应有 2 条运行记录
    import json

    lines = (tmp_path / "r2" / "memory" / "episodic" / "runs.jsonl").read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(x)["passed"] for x in lines)


def test_llm_arm_is_marked_requiring_key() -> None:
    llm = next(a for a in default_arms() if a.name == "llm")
    assert llm.requires_llm is True


def test_run_arm_on_tiny_task_set(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """真跑一个最小臂：确认执行器能把任务跑完并产出报告（不依赖 key）。"""
    tasks = [
        EvalTask(id="T-OK", kind="verify", goal="验证车门故障必须触发降级处置",
                 expect_fault="door_fault"),
        EvalTask(id="T-NEG", kind="honest_fail", goal="帮我写一首关于春天的诗"),
    ]
    rep = run_arm(Arm("rule", {"offline": True}), tasks, knowledge=knowledge,
                  workdir=tmp_path / "rule")
    assert rep.model_kind == "offline-rule"
    assert rep.metrics["tasks"] == 2
    assert rep.metrics["matched"] == 2, [v.reason for v in rep.verdicts]
    assert rep.metrics["hallucination_rate"] == 0.0


def test_arm_isolation_of_memory_dir(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """每臂独立记忆目录：否则后跑的臂会读到前一个臂的日志，对照失效。"""
    tasks = [EvalTask(id="T1", kind="verify", goal="验证车门故障必须触发降级处置",
                      expect_fault="door_fault")]
    run_arm(Arm("a", {"offline": True}), tasks, knowledge=knowledge, workdir=tmp_path / "a")
    run_arm(Arm("b", {"offline": True}), tasks, knowledge=knowledge, workdir=tmp_path / "b")
    ja = (tmp_path / "a" / "memory" / "episodic" / "runs.jsonl")
    jb = (tmp_path / "b" / "memory" / "episodic" / "runs.jsonl")
    assert ja.is_file() and jb.is_file()
    assert len(ja.read_text(encoding="utf-8").strip().splitlines()) == 1
    assert len(jb.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_report_is_json_serialisable(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    tasks = [EvalTask(id="T1", kind="honest_fail", goal="帮我写一首关于春天的诗")]
    rep = run_arm(Arm("rule", {"offline": True}), tasks, knowledge=knowledge,
                  workdir=tmp_path / "r")
    blob = json.dumps(rep.to_dict(), ensure_ascii=False)
    assert "verdicts" in blob and "metrics" in blob


def test_baseline_config_is_not_mutated(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """臂的覆盖不能污染调用方传入的 base_cfg（否则第二次评测结果会莫名变化）。"""
    base = AgentConfig(offline=True, memory_dir=Path("/nonexistent-should-stay"))
    tasks = [EvalTask(id="T1", kind="honest_fail", goal="帮我写一首关于春天的诗")]
    run_arm(Arm("rule", {"offline": True}), tasks, base_cfg=base, knowledge=knowledge,
            workdir=tmp_path / "x")
    assert base.memory_dir == Path("/nonexistent-should-stay")
    assert base.offline is True


def test_agent_runner_still_works_with_eval_imports(cfg: AgentConfig, knowledge: KnowledgeContext) -> None:
    """冒烟：评测所需的 outcome 字段确实被写进了证据里。"""
    res = AgentRunner(cfg, knowledge).run("验证车门故障必须触发降级处置", thread_id="eval-smoke")
    drafts = [e for e in res.evidence if e.get("tool") == "draft_test_case"]
    assert drafts and drafts[0].get("outcome"), "证据里应含工具结论摘要（评测判定依赖它）"
    assert drafts[0]["outcome"].get("draft_id")


# ---------------------------------------------------------------------------
# CI 回归门禁：基线不得跌破阈值
# ---------------------------------------------------------------------------


def test_baseline_task_set_gate(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """**这是真正的回归门禁**：规则臂在完整任务集上必须达标。

    阈值取「≥10/11 且零幻觉」——即首轮评测的实测基线，允许 1 条已知缺口
    （`T-VERIFY-CRC-LOOSE`：口语省略措辞，规则解析器依赖与资产命名的重合度）。
    今后任何改动只要让基线掉下来，这条测试就会响。

    代价：这条测试真跑 11 条任务（约 20 秒）。对一个覆盖 15 个工具、4 级权限、
    多层记忆的 Agent 来说，这是很便宜的护栏——尤其是它抓过两个真 bug。
    """
    rep = run_arm(Arm("rule", {"offline": True}), knowledge=knowledge, workdir=tmp_path / "gate")
    m = rep.metrics
    assert m["tasks"] == len(load_tasks())
    assert m["hallucination_rate"] == 0.0, f"基线出现幻觉引用: {m['refs_fabricated']} 条"
    assert m["match_rate"] >= 10 / 11, (
        f"基线跌破：{m['matched']}/{m['tasks']}；未达成："
        + "、".join(v.task_id for v in rep.verdicts if not v.matched)
    )


def test_gate_only_known_task_may_fail(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """失败项必须**恰好**是那条已知缺口，不能悄悄多出别的失败。"""
    rep = run_arm(Arm("rule", {"offline": True}), knowledge=knowledge, workdir=tmp_path / "gate2")
    failed = sorted(v.task_id for v in rep.verdicts if not v.matched)
    assert failed == ["T-VERIFY-CRC-LOOSE"], f"出现了预期外的失败任务: {failed}"
    loose = next(v for v in rep.verdicts if v.task_id == "T-VERIFY-CRC-LOOSE")
    assert "goal_fault=''" in loose.reason, f"缺口原因应是解析失败: {loose.reason}"
