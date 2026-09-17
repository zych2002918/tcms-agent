"""compose_seq（时序连锁原子化）门禁：先A后B随后C最后D → 逐原子故障 + 诚实未锚定。"""

from __future__ import annotations

from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@NEEDS_UPSTREAM
def _model():
    from tcms_ai_platform.core import load_asset_model

    return load_asset_model(UPSTREAM)


@NEEDS_UPSTREAM
def test_sequential_cascade_parses_atoms_and_honest_unresolved():
    """『先车门故障，后空调失效，随后牵引失效，最后紧急制动』：
    车门故障被锚定；空调/牵引子句未锚定时给域候选(不自动发明)；末句收尾期望=emergency_brake。"""
    from tcms_ai_platform.agent.composer import plan_compose_seq

    m = _model()
    s = plan_compose_seq(m, "先车门故障，后空调失效，随后牵引失效，最后紧急制动")
    assert s["keys"] == ["door_fault"]
    assert s["final_action"] == "emergency_brake"
    clauses = [u["clause"] for u in s["unresolved"]]
    assert len(clauses) >= 2, "空调/牵引子句应作为未锚定候选返回，而不是静默丢弃"
    for u in s["unresolved"]:
        dc = u.get("domain_candidates")
        assert dc is not None and dc.get("faults"), f"未锚定子句应带域候选: {u['clause']}"
        for f in dc["faults"]:
            assert f["key"] in m.faults_by_key, "域候选必须是真实故障键"


@NEEDS_UPSTREAM
def test_two_matched_faults_preserve_order():
    """两个都能锚定时按句序保留，且无未锚定项。"""
    from tcms_ai_platform.agent.composer import plan_compose_seq

    m = _model()
    s = plan_compose_seq(m, "车门故障，随后超速")
    assert s["keys"] == ["door_fault", "overspeed"]
    assert s["unresolved"] == []
    assert s["final_action"] is None


@NEEDS_UPSTREAM
def test_no_fault_raises_honest_error():
    from tcms_ai_platform.agent.composer import ComposeError, plan_compose_seq

    m = _model()
    with pytest.raises(ComposeError):
        plan_compose_seq(m, "今天天气不错")


@NEEDS_UPSTREAM
def test_pick_one_keeps_other_unresolved():
    """用户点选一个未锚定子句 → 只并这一个（按原句位置），其余未锚定子句保留继续可点。"""
    from tcms_ai_platform.agent.composer import plan_compose_seq

    m = _model()
    s = plan_compose_seq(m, "先车门故障，后空调失效，随后牵引失效，最后紧急制动")
    assert [u["clause"] for u in s["unresolved"]] == ["后空调失效", "牵引失效"]
    hvac_dc = s["unresolved"][0]["domain_candidates"]["faults"]
    pick_key = hvac_dc[0]["key"]
    assert pick_key in m.faults_by_key

    s2 = plan_compose_seq(
        m, "先车门故障，后空调失效，随后牵引失效，最后紧急制动",
        picks=[{"clause": "后空调失效", "key": pick_key}],
    )
    # 只并 1 个，按它在句中的位置排在 door 之后；牵引失效必须还在 unresolved 里
    assert s2["keys"] == ["door_fault", pick_key]
    assert [u["clause"] for u in s2["unresolved"]] == ["牵引失效"]
    assert s2["picked_count"] == 1
    # 再并第二个 → 全部锚定，按原句顺序 door → hvac → overspeed/牵引域候选
    s3 = plan_compose_seq(
        m, "先车门故障，后空调失效，随后牵引失效，最后紧急制动",
        picks=[
            {"clause": "后空调失效", "key": pick_key},
            {"clause": "牵引失效", "key": s2["unresolved"][0]["domain_candidates"]["faults"][0]["key"]},
        ],
    )
    assert s3["keys"] == ["door_fault", pick_key, s2["unresolved"][0]["domain_candidates"]["faults"][0]["key"]]
    assert s3["unresolved"] == []
    assert s3["picked_count"] == 2


@NEEDS_UPSTREAM
def test_pick_rejects_out_of_clause_key():
    """诚实门禁：picks 里非该子句域候选的键不并入（不跨子句错位、不认任意键）。"""
    from tcms_ai_platform.agent.composer import plan_compose_seq

    m = _model()
    # 给『后空调失效』塞一个门域键 door_fault —— 不在空调候选里 → 拒绝
    s = plan_compose_seq(
        m, "先车门故障，后空调失效，随后牵引失效，最后紧急制动",
        picks=[{"clause": "后空调失效", "key": "door_fault"}],
    )
    assert s["keys"] == ["door_fault"]  # door 只来自首句规则命中，不被重复并入
    assert [u["clause"] for u in s["unresolved"]] == ["后空调失效", "牵引失效"]
    assert s["picked_count"] == 0
    # 不在任何子句文本上的 clause 也不并入
    s2 = plan_compose_seq(
        m, "先车门故障，后空调失效",
        picks=[{"clause": "不存在子句", "key": "overspeed"}],
    )
    assert s2["keys"] == ["door_fault"]
    assert [u["clause"] for u in s2["unresolved"]] == ["后空调失效"]


@NEEDS_UPSTREAM
def test_compose_seq_endpoint_multi_pick_no_disappear():
    """端到端（真实引擎）：『选一个候选、另一个不消失』——先并空调，再并牵引，
    每次响应里其余未锚定子句都保留，最终三故障按原句顺序真实执行全过。"""
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    app = TestClient(create_app(upstream=UPSTREAM))
    msg = "先车门故障，后空调失效，随后牵引失效，最后紧急制动"

    b0 = app.post("/api/agent/compose_seq", json={"message": msg}).json()
    assert b0["faults"] == ["door_fault"]
    clauses = [u["clause"] for u in b0["unresolved"]]
    assert clauses == ["后空调失效", "牵引失效"]

    hvac_key = b0["unresolved"][0]["domain_candidates"]["faults"][0]["key"]
    b1 = app.post(
        "/api/agent/compose_seq",
        json={"message": msg, "picks": [{"clause": "后空调失效", "key": hvac_key}]},
    ).json()
    assert b1["faults"] == ["door_fault", hvac_key], "并入后按原句位置排在 door 之后"
    assert [u["clause"] for u in b1["unresolved"]] == ["牵引失效"], "另一个未锚定子句必须保留"

    tr_key = b1["unresolved"][0]["domain_candidates"]["faults"][0]["key"]
    b2 = app.post(
        "/api/agent/compose_seq",
        json={
            "message": msg,
            "picks": [
                {"clause": "后空调失效", "key": hvac_key},
                {"clause": "牵引失效", "key": tr_key},
            ],
        },
    ).json()
    assert b2["faults"] == ["door_fault", hvac_key, tr_key], "逐句并入后三故障按原句顺序"
    assert b2.get("unresolved", []) == []  # 全部锚定后不再有未锚定子句
    assert b2["final_action"] == "emergency_brake"
    assert b2["run"]["all_passed"] is True and b2["run"]["passed"] == 3
    # 演示顺序：门 1.0s → 空调 4.0s → 牵引 7.0s，再依次 recover
    inj = [s["fault"] for s in b2["steps"] if s["action"] == "inject"]
    rec = [s["fault"] for s in b2["steps"] if s["action"] == "recover"]
    assert inj == ["door_fault", hvac_key, tr_key]
    assert rec == ["door_fault", hvac_key, tr_key]


@NEEDS_UPSTREAM
def test_compose_seq_interlock_note_on_door_traction_eb():
    """门/牵引域故障 + 收尾 EB 期望 → 联锁联合提示（处置取决于原因 + 现成联锁场景）。"""
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    app = TestClient(create_app(upstream=UPSTREAM))
    msg = "先车门故障，后空调失效，随后牵引失效，最后紧急制动"
    picks = [
        {"clause": "后空调失效", "key": "hvac_airflow_low_cabin"},
        {"clause": "牵引失效", "key": "traction_loss"},
    ]
    b = app.post("/api/agent/compose_seq", json={"message": msg, "picks": picks}).json()
    assert b["final_action"] == "emergency_brake"
    assert b["interlock_note"], "门/牵引域 + EB 收尾应带处置取决于原因的联锁提示"
    files = {s["file"] for s in b["interlock_scenarios"]}
    assert "integrity_loss_eb_loop.yaml" in files, "应引用列车完整性丧失联锁场景"
    assert "door_open_moving_eb.yaml" in files, "应引用运行中车门打开联锁场景"
    # 无 EB 收尾 → 不给联锁提示（不误标）
    b2 = app.post("/api/agent/compose_seq", json={"message": "车门故障，随后超速"}).json()
    assert b2.get("final_action") is None
    assert not b2.get("interlock_note")
