"""Q3 原子组合器测试：一句话多故障意图 → 计划 + 溯源；run=True → 真实引擎执行。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tcms_ai_platform.agent.composer import ComposeError, plan_compose
from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def model():
    return load_asset_model(UPSTREAM)


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    return TestClient(create_app(upstream=UPSTREAM))


@NEEDS_UPSTREAM
def test_plan_compose_multifault(model):
    """「车门故障 + 超速 + 烟火报警」组合 → 全部真实故障、步骤可执行。"""
    plan = plan_compose(
        model, "请把车门故障、超速和烟火报警组合成一个级联场景"
    )
    assert set(plan["faults"]) == {"door_fault", "overspeed", "smoke_detected"}
    assert plan["summary"]["fault_count"] == 3
    inj = [s for s in plan["steps"] if s["action"] == "inject"]
    rec = [s for s in plan["steps"] if s["action"] == "recover"]
    assert len(inj) == 3 and len(rec) == 3
    # 步骤可直接被引擎消费：真实故障键 + 期望 ∈ 5 动作 + 递增时序
    ats = [s["at"] for s in plan["steps"]]
    assert ats == sorted(ats)
    for s in inj:
        assert s["fault"] in model.faults_by_key
        assert s["expect"] in {"none", "warning", "derate", "emergency_brake", "shutdown"}
    # 溯源：每条故障都有源资产/系统/Agent 三栏 + 库内相似模板
    assert len(plan["provenance"]) == 3
    for p in plan["provenance"]:
        assert p["source_asset"]["action"]
        assert p["source_system"]["domain"]
        assert p["source_agent"]
    assert plan["summary"]["systems"]  # 覆盖多系统域


@NEEDS_UPSTREAM
def test_plan_compose_empty_or_unmatched(model):
    """空目标 / 无故障语义 → ComposeError（不猜不造，OK=False 引导）。"""
    with pytest.raises(ComposeError):
        plan_compose(model, "   ")
    with pytest.raises(ComposeError):
        plan_compose(model, "今天晚饭吃什么比较好")


@NEEDS_UPSTREAM
def test_api_compose_plan_only(client):
    r = client.post("/api/agent/composer", json={"goal": "辅助变流器故障加低压过流，组合序列"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert set(body["faults"]) >= {"aux_converter_fault"}
    assert body["provenance"]
    assert "execution" not in body


@NEEDS_UPSTREAM
def test_api_compose_run_executes_real_engine(client):
    """run=True：组合计划 → 真实引擎执行 → 全部断言通过（③验收主线）。"""
    r = client.post(
        "/api/agent/composer",
        json={"goal": "车门故障加超速级联，最后接烟火报警停车", "run": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["summary"]["fault_count"] >= 3
    ex = body["execution"]
    assert ex["all_passed"] is True
    assert ex["passed"] == body["summary"]["fault_count"]
    assert ex["failed"] == 0
    assert ex["engine_version"]  # 引擎 v1.12.x 自证
    # 溯源在计划里逐条可见
    assert len(body["provenance"]) == body["summary"]["fault_count"]


@NEEDS_UPSTREAM
def test_api_compose_unmatched_ok_false(client):
    r = client.post("/api/agent/composer", json={"goal": "晚上吃什么"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "未识别" in (body["reason"] or "")


@NEEDS_UPSTREAM
def test_composer_multiturn_history(model):
    """④-d 多轮上下文：当前句用指代（无点名）→ 回看 history 把前文故障续编进来。"""
    plan = plan_compose(
        model,
        "刚才那两个也一起组合进来吧",
        history=["先看看轴温过高那个", "另外超速也要处理"],
    )
    assert {"bogie_axle_overheat", "overspeed"} <= set(plan["faults"])
    assert plan["summary"].get("history_resolved") is True
    # 当前句直接点名时不依赖 history
    plan2 = plan_compose(model, "烟火报警停车", history=["轴温过高"])
    assert set(plan2["faults"]) == {"smoke_detected"}
    assert not plan2["summary"].get("history_resolved")


@NEEDS_UPSTREAM
def test_api_compose_multiturn_history(client):
    r = client.post(
        "/api/agent/composer",
        json={"goal": "刚才提到的后门那个也加上", "history": ["车门故障加超速", "后车门故障", "再补烟火报警"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["summary"].get("history_resolved") is True
    assert "rear_door_fault" in body["faults"]
    assert body["provenance"]
