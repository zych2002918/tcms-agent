"""P4 测试：Agent 任务库 + Harness 工作流 + 评分（真实引擎执行）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.agent import (
    AgentHarness,
    LLMAgentBackend,
    MockAgentBackend,
    TaskDef,
    default_tasks,
    llm_available,
)
from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.knowledge import (
    HybridRetriever,
    VectorStore,
    build_docs_from_asset,
    build_knowledge_graph,
)

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def harness():
    # 使 tcms 引擎可 import（真实执行）
    import sys

    if str(UPSTREAM) not in sys.path:
        sys.path.insert(0, str(UPSTREAM))
    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    store = VectorStore()
    store.add_many(build_docs_from_asset(m))
    retriever = HybridRetriever(store, g)
    return AgentHarness(m, retriever, UPSTREAM / "scenarios", backend=MockAgentBackend())


@NEEDS_UPSTREAM
def test_default_tasks_anchored_real_faults():
    """任务库全部锚定真实故障字典且期望处置一致（漂移即失败）。"""
    m = load_asset_model(UPSTREAM)
    tasks = default_tasks(m)
    assert len(tasks) == 8
    ids = {t.task_id for t in tasks}
    assert ids == {
        "T-EBM",
        "T-DOOR",
        "T-OVERSPEED",
        "T-CONFLICT",
        "T-HEARTBEAT",
        "T-BUS",
        "T-CRC",
        "T-STORM",
    }
    # 每个任务的目标故障必须真实存在且期望处置与故障字典一致
    for t in tasks:
        f = m.faults_by_key[t.target_fault]
        assert f.action == t.expected_action


@NEEDS_UPSTREAM
def test_harness_runs_task_with_trace(harness):
    """单任务完整循环：检索证据 → 计划 → 真实执行 → 验证 → 轨迹。"""
    tasks = default_tasks(harness.model)
    run = harness.run_task(next(t for t in tasks if t.task_id == "T-DOOR"))
    assert run.achieved is True
    assert run.plan is not None
    assert run.plan.fault == "door_fault"
    assert run.execution is not None
    assert run.execution.get("all_passed") is True
    assert run.evidence  # 用上了知识底座
    assert run.trace  # 轨迹非空
    steps = [t["step"] for t in run.trace]
    assert "plan" in steps and "exec" in steps and "verify" in steps
    assert run.score()["achieved"] is True


@NEEDS_UPSTREAM
def test_harness_full_suite(harness):
    """全部任务跑通（mock 后端确定性 → 全达成）。"""
    tasks = default_tasks(harness.model)
    res = harness.run_tasks(tasks)
    assert res["total"] == 8
    assert res["achieved"] == 8
    assert res["success_rate"] == 1.0
    for r in res["runs"]:
        assert r["achieved"] is True
        assert r["score"]["score"] >= 60
        assert "plan" in {t["step"] for t in r["trace"]}


@NEEDS_UPSTREAM
def test_mock_backend_picks_covering_scenario():
    """Mock 后端确定性选择覆盖该故障的场景。"""
    m = load_asset_model(UPSTREAM)
    backend = MockAgentBackend()
    task = next(t for t in default_tasks(m) if t.task_id == "T-OVERSPEED")
    scenarios = [{"file": s.file, "fault_keys": list(s.fault_keys)} for s in m.scenarios.values()]
    plan = backend.plan(task, [], scenarios)
    assert plan.chosen_scenario
    # 所选场景确实覆盖 overspeed
    scen = next(s for s in scenarios if s["file"] == plan.chosen_scenario)
    assert "overspeed" in scen["fault_keys"]


@NEEDS_UPSTREAM
def test_run_tasks_include_review(harness):
    """run_tasks 输出应含基于真实领域知识的评审（evaluator-optimizer）。"""
    res = harness.run_tasks(default_tasks(harness.model))
    assert res["review_passed"] == res["total"]  # 真实任务应全过评审
    for r in res["runs"]:
        assert "review" in r
        rv = r["review"]
        assert rv["passed"] is True
        # 6 个评审维度都在
        assert set(rv["dimensions"].keys()) >= {
            "result_grounded",
            "evidence_used",
            "threshold_aware",
            "domain_aware",
            "requirement_trace",
            "honesty",
        }


def test_llm_available_false_without_key(monkeypatch, tmp_path):
    """无 key(env/settings/凭据文件均无)时 llm_available=False（离线确定性）。"""
    for k in ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path / "home"))  # 隔离设置层（GUI 存的 key 不入测）
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(Path("Z:/no-cred.yaml")))
    assert llm_available() is False


def test_llm_backend_falls_back_to_mock_without_key(monkeypatch, tmp_path):
    """LLM 后端无 key(env/settings/凭据文件均无) → 自动落回 Mock（确定性选场景）。"""
    for k in ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path / "home"))  # 隔离设置层
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(Path("Z:/no-cred.yaml")))
    backend = LLMAgentBackend()
    task = TaskDef(
        task_id="T-X",
        title="t",
        goal="g",
        target_fault="overspeed",
        expected_action="derate",
    )
    scenarios = [
        {"file": "door_cascade.yaml", "fault_keys": ["door_fault", "overspeed"]},
        {"file": "overspeed_derate.yaml", "fault_keys": ["overspeed"]},
    ]
    plan = backend.plan(task, [], scenarios)
    assert plan.chosen_scenario  # mock 落回仍选出覆盖场景
    assert plan.expected_action == "derate"
    assert backend.used_llm is False  # 无 key → 未用 LLM
