"""端到端：离线规则臂跑完整图（必须 100% 可复现）。

这些用例是整个 agent 包的**回归门禁**：它们不依赖任何 API key，
因此可以无条件进 CI。
"""

from __future__ import annotations

from tcms_agent.runner import AgentRunner

GOAL = "验证车门故障必须触发降级处置"


def test_offline_run_passes_and_is_honestly_labelled(cfg, knowledge) -> None:
    res = AgentRunner(cfg, knowledge).run(GOAL)
    assert res.model_kind == "offline-rule", "无 key 时必须如实标注为离线规则臂"
    assert res.passed is True, f"应通过，未通过原因: {res.verdict.get('reasons')}"
    assert res.verdict["refs_fabricated"] == []
    assert res.verdict["goal_fault"] == "door_fault"
    assert res.verdict["fault_covered"] is True
    assert res.verdict["refs_checked"] >= 3
    assert res.verdict["evidence_count"] >= 3


def test_trajectory_has_full_node_coverage(cfg, knowledge) -> None:
    res = AgentRunner(cfg, knowledge).run(GOAL)
    nodes = {t["node"] for t in res.trace}
    assert {"plan", "agent", "act", "verify", "report"} <= nodes, (
        f"轨迹应覆盖全部节点，实际: {sorted(nodes)}"
    )
    events = [t["event"] for t in res.trace]
    assert "plan" in events and "observe" in events and "verify" in events


def test_tool_audit_is_recorded(cfg, knowledge) -> None:
    res = AgentRunner(cfg, knowledge).run(GOAL)
    assert res.audit["calls"] >= 4, "离线臂现在也会做真执行验证"
    assert res.audit["failed"] == 0
    # 默认档位 = R0 只读 + R1 沙箱写 + R2 真实执行；不得出现任何 R3 持久化调用
    assert set(res.audit["by_level"]) == {"R0 只读", "R1 沙箱写", "R2 真实执行"}
    assert len(res.audit["tools_available"]) == 13, "9 只读 + 1 沙箱写 + 3 执行"


def test_run_is_deterministic_without_memory(cfg, knowledge) -> None:
    """在**记忆关闭**时，两次运行的决策序列与引用集合完全一致。

    为什么要显式关掉记忆：记忆一旦开启，第二次运行会召回第一次的结果，
    轨迹就**应当**不同——那是记忆在起作用，不是不确定性。可复现性的准确表述是
    "同一配置 + 同一记忆状态下可复现"，见下一条测试。
    """
    off = cfg.with_(memory_enabled=False)
    runner = AgentRunner(off, knowledge)
    a = runner.run(GOAL, thread_id="det-a")
    b = runner.run(GOAL, thread_id="det-b")
    seq_a = [(t["node"], t["event"], t["detail"]) for t in a.trace]
    seq_b = [(t["node"], t["event"], t["detail"]) for t in b.trace]
    assert seq_a == seq_b
    assert a.sources == b.sources
    assert a.verdict["passed"] == b.verdict["passed"]


def test_memory_makes_runs_path_dependent(cfg, knowledge) -> None:
    """开启记忆后，第二次运行的轨迹**应当**与第一次不同（记忆确实参与了）。"""
    runner = AgentRunner(cfg, knowledge)
    a = runner.run(GOAL, thread_id="mem-a")
    b = runner.run(GOAL, thread_id="mem-b")
    assert a.memory_hits == []
    assert b.memory_hits, "第二次运行应召回第一次"
    recall_a = next(t["detail"] for t in a.trace if t["node"] == "recall")
    recall_b = next(t["detail"] for t in b.trace if t["node"] == "recall")
    assert recall_a != recall_b, "轨迹差异来自记忆召回，这正是记忆在起作用"


def test_different_goals_resolve_to_their_own_faults(cfg, knowledge) -> None:
    runner = AgentRunner(cfg, knowledge)
    for goal, fault in (
        ("验证超速必须触发降级", "overspeed"),
        ("验证 VCU 心跳丢失要降级", "heartbeat_loss_vcu"),
    ):
        res = runner.run(goal)
        assert res.verdict["goal_fault"] == fault, f"{goal} 应解析为 {fault}"
        assert res.passed is True, res.verdict.get("reasons")


def test_unparseable_goal_fails_honestly_instead_of_faking(cfg, knowledge) -> None:
    """目标与 TCMS 无关时必须如实不通过——绝不硬猜一个故障键。"""
    res = AgentRunner(cfg, knowledge).run("今天天气不错，帮我看看")
    assert res.passed is False
    assert res.verdict["goal_fault"] == ""
    reasons = " ".join(res.verdict["reasons"])
    assert "未能从目标中解析出真实故障键" in reasons


def test_step_budget_is_enforced(cfg, knowledge) -> None:
    """预算用尽必须停手，且不得因为"答得像"就判通过。"""
    tight = cfg.with_(max_steps=1)
    res = AgentRunner(tight, knowledge).run(GOAL)
    assert res.steps <= tight.max_steps + 1, f"步数应受控，实际 {res.steps}"
    assert res.passed is False, "预算耗尽时目标故障未被覆盖，不应判通过"
    assert any("预算" in t["detail"] for t in res.trace)


def test_no_fabricated_reference_across_all_goals(cfg, knowledge) -> None:
    """跨多个目标自证：引用校验器不应放过任何编造（也不应误杀真实）。"""
    runner = AgentRunner(cfg, knowledge)
    for goal in ("验证车门故障不能发车", "验证超速降级", "验证总线短路停车"):
        res = runner.run(goal)
        assert res.verdict["refs_fabricated"] == [], f"{goal} 出现幻觉引用"
        for ref in res.verdict["refs"]:
            assert ref["exists"] is True
