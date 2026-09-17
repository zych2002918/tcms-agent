"""t2 扩展测试：FaultLab 任意序列动画（/api/faultlab/demo-steps）。

从「只能按已存场景文件名演示」升级为「任意故障序列 → 组合生成动画」：
    - 合法序列（未知故障校验通过）→ 动画含 source/pipeline/engine 全字段
    - 未知 fault → 422 中文
    - 无引擎 → 不 503，仍出动画（engine_asserted=false，诚实标注）
    - /api/faultlab/demo 传 steps 复用 demo-steps 逻辑（向后兼容）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

# 合法多故障序列（与 ScenarioStep 同构：门故障 + 超速 级联）
STEPS_OK = [
    {
        "at": 1.0,
        "action": "inject",
        "fault": "door_fault",
        "node": "bcu",
        "level": "major",
        "expect": "derate",
        "impact": "车门状态不可信",
    },
    {
        "at": 2.0,
        "action": "inject",
        "fault": "overspeed",
        "node": "vcu",
        "level": "major",
        "expect": "derate",
        "impact": "速度超限",
    },
    {"at": 30.0, "action": "recover", "fault": "door_fault"},
    {"at": 31.0, "action": "recover", "fault": "overspeed"},
]


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    app = create_app(upstream=UPSTREAM)
    from fastapi.testclient import TestClient

    return TestClient(app)


@NEEDS_UPSTREAM
def test_demo_steps_valid_animation(client):
    """合法序列 → 200：动画含 scenario=custom/<name>、events/curve、pipeline、engine。"""
    r = client.post("/api/faultlab/demo-steps", json={"name": "门故序列", "steps": STEPS_OK})
    assert r.status_code == 200
    body = r.json()
    demo = body["demo"]
    assert demo["scenario"] == "custom/门故序列"
    assert demo["scenario_name"] == "门故序列"
    assert demo["faults"] == ["door_fault", "overspeed"]
    assert demo["steps"] == 4
    # 事件时间线完整（注入/检测/处置/恢复）+ 引擎观察窗来源标注
    kinds = {e["kind"] for e in demo["events"]}
    assert {"inject", "detect", "action", "recover"} <= kinds
    inj = next(e for e in demo["events"] if e["kind"] == "inject")
    # source.scenario_yaml 的 ref 对自定义序列显示 custom/<name>（诚实标注非资产）
    assert inj["source"]["kind"] == "scenario_yaml"
    assert "custom/门故序列" in inj["source"]["ref"]
    assert "自定义" in inj["source"]["desc"]
    # 处置动作走真实字典/引擎
    acts = [e for e in demo["events"] if e["kind"] == "action"]
    assert any(e["action"] == "derate" for e in acts)
    # 曲线与管线齐备（透明自证）
    assert body["curve"]
    assert demo["pipeline"]["title"]
    assert demo["pipeline"]["constants"]["real"]
    assert demo["pipeline"]["constants"]["schematic"]
    assert "engine" in demo


@NEEDS_UPSTREAM
def test_demo_steps_engine_window(client):
    """引擎可用（测试夹具挂上游）→ demo-steps 先真实执行该序列，断言来源真实。"""
    r = client.post("/api/faultlab/demo-steps", json={"name": "s", "steps": STEPS_OK})
    assert r.status_code == 200
    body = r.json()
    assert body["engine_asserted"] is True
    eng = body["demo"]["engine"]
    assert eng["asserted"] is True
    assert eng["version"]  # 真实引擎版本（_run_custom_steps 已补 engine_version）
    act = next(e for e in body["demo"]["events"] if e["kind"] == "action")
    assert act["source"]["kind"] == "engine_assert"
    assert "run_result.assertions" in act["source"]["ref"]


@NEEDS_UPSTREAM
def test_demo_steps_unknown_fault_422(client):
    """未知故障键 → 422 中文（防拼写错误静默通过）。"""
    r = client.post(
        "/api/faultlab/demo-steps",
        json={"steps": [{"at": 1.0, "action": "inject", "fault": "no_such_fault"}]},
    )
    assert r.status_code == 422
    assert "未知故障键" in r.json()["detail"]


@NEEDS_UPSTREAM
def test_demo_steps_empty_422(client):
    """空步骤 → 422。"""
    r = client.post("/api/faultlab/demo-steps", json={"name": "空", "steps": []})
    assert r.status_code == 422


@NEEDS_UPSTREAM
def test_demo_steps_bad_action_422(client):
    """未知动作 → 422。"""
    r = client.post(
        "/api/faultlab/demo-steps",
        json={"steps": [{"at": 1.0, "action": "explode", "fault": "overspeed"}]},
    )
    assert r.status_code == 422
    assert "未知动作" in r.json()["detail"]


@NEEDS_UPSTREAM
def test_demo_minimal_steps_omitted_optional_fields(client):
    """步骤缺省可选字段（node/level/expect/impact 全省略）也能出动画（引擎兜底）。"""
    r = client.post(
        "/api/faultlab/demo-steps",
        json={"steps": [{"at": 1.0, "action": "inject", "fault": "overspeed"}]},
    )
    assert r.status_code == 200
    demo = r.json()["demo"]
    assert demo["faults"] == ["overspeed"]
    acts = [e for e in demo["events"] if e["kind"] == "action"]
    assert acts and acts[0]["action"] == "derate"  # 字典默认 action 兜底


@NEEDS_UPSTREAM
def test_build_demo_from_steps_without_engine_honest_degrade():
    """无引擎（run_result=None）→ 仍出动画：engine.asserted=false + 处置回退
    故障字典并诚实标注 fault_dict（demo-steps 主要用途是"看动画"）。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.faultlab import build_curve, build_demo_from_steps

    m = load_asset_model(UPSTREAM)
    demo = build_demo_from_steps(
        m,
        "降级演示",
        [{"at": 1.0, "action": "inject", "fault": "overspeed", "expect": "derate"}],
        run_result=None,
    )
    assert demo["scenario"] == "custom/降级演示"
    assert demo["engine"]["asserted"] is False
    assert demo["engine"]["version"] is None
    assert any("run_result=None" in n for n in demo["engine"]["notes"])
    # 处置 actual 回退真实故障字典（诚实标注 fault_dict）
    act = next(e for e in demo["events"] if e["kind"] == "action")
    assert act["action"] == "derate"
    assert act["source"]["kind"] == "fault_dict"
    assert "faults.yaml#overspeed.action" in act["source"]["ref"]
    # 曲线照常生成（动画可播）
    assert build_curve(m, demo)


@NEEDS_UPSTREAM
def test_faultlab_demo_accepts_steps_backward_compat(client):
    """旧端点 /api/faultlab/demo 传 steps → 复用 demo-steps 逻辑（向后兼容）。"""
    r = client.post(
        "/api/faultlab/demo",
        json={"name": "seq", "steps": [{"at": 1.0, "action": "inject", "fault": "overspeed"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["demo"]["scenario"] == "custom/seq"
    assert body["demo"]["faults"] == ["overspeed"]
    # 真实场景文件路径仍工作（回归锁定）
    r2 = client.post("/api/faultlab/demo", json={"scenario": "door_cascade.yaml"})
    assert r2.status_code == 200
    assert r2.json()["demo"]["scenario"] == "door_cascade.yaml"
    # 两字段都不给 → 422（引导）
    r3 = client.post("/api/faultlab/demo", json={})
    assert r3.status_code == 422


@NEEDS_UPSTREAM
def test_faultlab_demo_steps_invalid_not_swallowed(client):
    """回归锁定：旧端点传非法 steps → 422（校验必须在引擎降级 try 之外，
    不得被吞成静默 200 的假动画）。"""
    r = client.post(
        "/api/faultlab/demo",
        json={"name": "bad", "steps": [{"at": 1.0, "action": "inject", "fault": "no_such_fault"}]},
    )
    assert r.status_code == 422
    assert "未知故障键" in r.json()["detail"]
