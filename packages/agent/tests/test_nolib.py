"""nolib 对照仪器：证明"框架买的是能力，不是结果"。

三条主张，每条都用事实验证而不是写在表里：
1. **同集结果一致** —— 框架不参与决策，因此不改变任务结果；
2. **能力确实缺失** —— nolib 不落盘、不能续跑、不能暂停等人；
3. **缺失被如实披露** —— nolib 拒绝审批时会留痕，绝不静默写入。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tcms_agent.config import AgentConfig
from tcms_agent.eval import EvalTask, run_arm
from tcms_agent.eval.harness import Arm
from tcms_agent.knowledge import KnowledgeContext
from tcms_agent.nolib import (
    capability_matrix,
    code_size,
    parity_report,
    render,
    run_goal,
    verify_capabilities,
)
from tcms_agent.permissions import Permission


def _cfg(tmp_path: Path, **kw) -> AgentConfig:
    return AgentConfig(
        offline=True,
        memory_enabled=False,
        db_path=tmp_path / "cp.sqlite",
        memory_dir=tmp_path / "memory",
        sandbox_dir=tmp_path / "sandbox",
        artifacts_dir=tmp_path / "artifacts",
        **kw,
    )


# ---------------------------------------------------------------------------
# 1) 同集结果一致
# ---------------------------------------------------------------------------


def test_nolib_produces_same_result_as_graph(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    goal = "验证车门故障必须触发降级处置"
    g = run_arm(Arm("graph", {"offline": True}), [EvalTask("T", "verify", goal, "door_fault")],
                knowledge=knowledge, workdir=tmp_path / "g")
    n = run_arm(Arm("nolib", {"offline": True}, engine="nolib"),
                [EvalTask("T", "verify", goal, "door_fault")],
                knowledge=knowledge, workdir=tmp_path / "n")
    assert g.metrics["matched"] == n.metrics["matched"] == 1
    assert g.metrics["avg_steps"] == n.metrics["avg_steps"]
    assert g.metrics["avg_tool_calls"] == n.metrics["avg_tool_calls"]


def test_nolib_trace_matches_graph_step_for_step(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """两版共用同一批节点，因此轨迹应当逐步一致（节点名 + 事件类型）。"""
    from tcms_agent.runner import AgentRunner

    goal = "验证车门故障必须触发降级处置"
    g_res = AgentRunner(_cfg(tmp_path / "g"), knowledge).run(goal, thread_id="parity-g")
    n_res = run_goal(goal, cfg=_cfg(tmp_path / "n"), knowledge=knowledge, run_id="parity-n")

    seq_g = [(t["node"], t["event"]) for t in g_res.trace]
    seq_n = [(t["node"], t["event"]) for t in n_res.trace]
    assert seq_n, "nolib 应有轨迹"
    assert seq_g == seq_n, f"轨迹序列应完全一致\n graph={seq_g}\n nolib={seq_n}"


# ---------------------------------------------------------------------------
# 2) 能力确实缺失（用事实验证）
# ---------------------------------------------------------------------------


def test_nolib_writes_no_checkpoint(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    res = run_goal("验证车门故障必须触发降级处置", cfg=cfg, knowledge=knowledge, run_id="n1")
    assert res.passed is True, "任务仍能达成"
    assert not Path(cfg.db_path).is_file(), "nolib 不提供状态落盘——这正是框架的价值"


def test_graph_writes_checkpoint_and_supports_replay(
    knowledge: KnowledgeContext, tmp_path: Path
) -> None:
    from tcms_agent.runner import AgentRunner

    cfg = _cfg(tmp_path / "g")
    runner = AgentRunner(cfg, knowledge)
    runner.run("验证车门故障必须触发降级处置", thread_id="g1")
    db = Path(cfg.db_path)
    assert db.is_file()
    with sqlite3.connect(str(db)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert any("checkpoint" in t for t in tables), f"应有 checkpoint 表: {sorted(tables)}"
    assert len(runner.history("g1")) >= 5, "框架版支持逐超级步回放"


def test_verify_capabilities_all_pass(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """8 项能力断言全过——这是"框架提供了什么"的机器证据。"""
    checks = verify_capabilities(AgentConfig(offline=True), knowledge, tmp_path)
    failed = [k for k, v in checks.items() if not v]
    assert failed == [], f"能力验证未通过: {failed}"
    assert checks["graph_writes_checkpoint"] and checks["graph_has_replay"]
    assert checks["nolib_writes_no_checkpoint"]
    assert checks["nolib_auto_rejects_approval"]


# ---------------------------------------------------------------------------
# 3) 缺失被如实披露，绝不静默
# ---------------------------------------------------------------------------


def test_nolib_auto_rejects_approval_and_never_silently_writes(
    knowledge: KnowledgeContext, tmp_path: Path
) -> None:
    """nolib 无法暂停等人 → 必须**当场如实拒绝**，而不是假装成功或偷偷写入。"""
    cfg = _cfg(tmp_path, max_level=Permission.PERSIST)
    res = run_goal("验证车门故障必须触发降级处置", cfg=cfg, knowledge=knowledge, run_id="np")
    assert res.approvals, "应记录审批事件"
    assert all(not a["approved"] for a in res.approvals)
    assert "nolib" in str(res.approvals[0]["reason"])
    # 拒绝必须在轨迹里可见
    assert any("无法暂停" in str(t.get("detail", "")) for t in res.trace)
    # 关键：真的什么都没写
    assert not list((tmp_path / "memory").rglob("*.md")), "未获批准的写入绝不能落盘"


def test_graph_can_pause_but_nolib_cannot(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    """同一个需审批的目标：框架版能停下来等人，nolib 只能拒绝。"""
    from tcms_agent.runner import AgentRunner

    goal = "验证车门故障必须触发降级处置"
    cfg = _cfg(tmp_path / "g", max_level=Permission.PERSIST)
    seen: list[dict] = []

    def approver(payload: dict) -> dict:
        seen.append(payload)
        return {"approved": True, "reason": "测试批准"}

    res = AgentRunner(cfg, knowledge).run(goal, thread_id="pause", approver=approver)
    assert seen, "框架版应真的暂停并征求审批"
    assert any(a["approved"] for a in res.approvals)
    assert list((tmp_path / "g" / "memory").rglob("*.md")), "获批后应落盘"


# ---------------------------------------------------------------------------
# 报告与代码量
# ---------------------------------------------------------------------------


def test_code_size_reports_both_orchestrations() -> None:
    code = code_size()
    assert code["nolib_loop"] > 50, "nolib 应有真实实现"
    assert code["graph"] > 20 and code["runner"] > 50
    assert code["nodes(共用)"] > 100, "节点是两版共用的，不应算作某一边的成本"


def test_capability_matrix_declares_the_gaps() -> None:
    caps = {c.name: c for c in capability_matrix()}
    for name in ("状态落盘", "断点续跑", "人工审批中断", "轨迹回放", "流式观测"):
        assert name in caps, f"能力对照缺少 {name}"
        assert caps[name].nolib.startswith("❌"), f"{name} 应如实标注 nolib 缺失"
    assert caps["任务结果"].nolib.startswith("✅"), "任务结果一致是本对照的核心结论"


def test_render_includes_parity_and_conclusion(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    from tcms_agent.eval import load_tasks

    parity = parity_report(load_tasks(), AgentConfig(offline=True), knowledge, tmp_path)
    text = render(code_size(), capability_matrix(), parity)
    assert "逐条一致" in text
    assert "框架不参与决策" in text


def test_parity_report_detects_identity(knowledge: KnowledgeContext, tmp_path: Path) -> None:
    tasks = [
        EvalTask("T1", "verify", "验证车门故障必须触发降级处置", "door_fault"),
        EvalTask("T2", "honest_fail", "帮我写一首关于春天的诗"),
    ]
    parity = parity_report(tasks, AgentConfig(offline=True), knowledge, tmp_path)
    assert parity["tasks"] == 2
    assert parity["identical"] is True, f"两版应逐条一致: {parity['mismatches']}"
    assert parity["graph_match_rate"] == parity["nolib_match_rate"]


def test_nolib_default_arm_available_offline() -> None:
    from tcms_agent.eval import default_arms

    nolib = next(a for a in default_arms() if a.name == "nolib")
    assert nolib.engine == "nolib"
    assert nolib.requires_llm is False, "nolib 对照必须能离线跑，否则进不了 CI"
    ok, why = nolib.available()
    assert ok, why
