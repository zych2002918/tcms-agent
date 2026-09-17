"""P0-1 API JSON 契约测试 —— 后端返回结构的“形状即契约”（防字段漂移/静默缺字段）。

覆盖四类关键负载的形状契约（不锁死具体值，锁定“必须有的结构”）：
1. /api/kb/overview —— 默认骨架图（system/function/fault 节点 + 带类型边）；
2. /api/scenarios —— 每个场景必须 name+desc 都非空（命名/释义链路契约）；
3. /api/faultlab/demo —— 事件必带基础字段，inject 事件必带“哪里/什么”字段
   （fault_name/subsystem/domain_zh）与 detail；
4. /api/agent/diagnose —— 症状诊断响应全字段契约（含 candidates/plan/evidence）。

新增端点或改事件字段时，本文件是“向后契约”的机器化守卫：缺字段即红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

_BASE_KEYS = {"t", "kind", "fault", "label", "detail", "level", "action", "derived"}


@pytest.fixture(scope="module")
def client():
    """真实上游加载的 app 测试客户端（与 test_platform 同构）。"""
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    return TestClient(create_app(upstream=UPSTREAM))


@NEEDS_UPSTREAM
def test_contract_kb_overview_skeleton(client):
    """默认骨架图契约：seed=overview、含 13 系统 + 11 功能 + 故障，边带类型。"""
    ov = client.get("/api/kb/overview?limit=3").json()
    assert ov["seed"] == "overview"
    assert ov["node_count"] == len(ov["nodes"])
    kinds = [n["kind"] for n in ov["nodes"]]
    assert kinds.count("system") == 13
    assert kinds.count("function") == 11
    assert kinds.count("fault") >= 1
    assert kinds.count("fault") + kinds.count("system") + kinds.count("function") == len(kinds)
    assert ov["edges"], "骨架图必须有边"
    assert all(e.get("src") and e.get("dst") and e.get("kind") for e in ov["edges"])
    assert ov["edge_count"] == len(ov["edges"])


@NEEDS_UPSTREAM
def test_contract_scenarios_name_and_desc(client):
    """场景列表契约：104 个场景 name 与 desc 全非空（命名/释义链路）。"""
    sc = client.get("/api/scenarios").json()
    assert len(sc) == 104
    for s in sc:
        assert (s.get("name") or "").strip(), f"{s['file']}: 缺 name"
        assert (s.get("desc") or "").strip(), f"{s['file']}: 缺 desc"


@NEEDS_UPSTREAM
def test_contract_faultlab_events_have_where_what(client):
    """FaultLab 事件契约：基础字段齐备；inject 事件带哪里/什么（真实字典派生）。"""
    body = client.post("/api/faultlab/demo", json={"scenario": "wave_c_aux.yaml"}).json()
    demo = body["demo"]
    assert demo["scenario_name"] and demo["duration"] > 0
    events = demo["events"]
    injects = [e for e in events if e["kind"] == "inject"]
    assert injects, "wave_c_aux 至少有一个注入事件"
    for e in events:
        assert _BASE_KEYS <= set(e), f"事件缺基础字段: {sorted(_BASE_KEYS - set(e))}"
        assert e["kind"] in {"inject", "detect", "action", "recover", "note"}
    for e in injects:
        assert e.get("fault_name"), "inject 事件缺 fault_name（具体什么异常）"
        assert e.get("subsystem"), "inject 事件缺 subsystem（哪里异常·子系统）"
        assert e.get("domain_zh"), "inject 事件缺 domain_zh（哪里异常·系统域）"
        assert e.get("detail"), "inject 事件缺 detail（现象说明）"


@NEEDS_UPSTREAM
def test_contract_diagnose_response_shape(client):
    """症状诊断响应契约：matched 场景下 candidate/plan/evidence 字段齐备。"""
    r = client.post("/api/agent/diagnose", json={"message": "仪表盘闪烁但无故障码"}).json()
    for key in ("query", "matched", "no_match", "symptom", "uncertain", "reply", "candidates", "plan", "evidence", "no_fault_code_invented"):
        assert key in r, f"诊断响应缺字段: {key}"
    assert r["matched"] is True and r["candidates"]
    for c in r["candidates"]:
        for key in ("fault", "name", "domain", "domain_zh", "hop", "basis", "confidence", "level", "action", "check", "scenarios", "derived"):
            assert key in c, f"候选缺字段: {key}（{c.get('fault')}）"
    assert r["plan"] and all(p.get("description") for p in r["plan"])


@NEEDS_UPSTREAM
def test_contract_kb_path_evidence(client):
    """证据图可达查询契约：dashboard→24V 欠压根因链 found=true 且逐边带依据。"""
    r = client.post(
        "/api/kb/path",
        json={"src": "symptom:dashboard_flicker", "dst": "fault:aux_24v_charger_fail", "max_depth": 4},
    ).json()
    assert r["found"] is True and r["hops"] >= 1
    for e in r["edges"]:
        assert {"src", "dst", "kind", "basis", "note"} <= set(e)
    # 未知节点 → 诚实 found=false
    miss = client.post("/api/kb/path", json={"src": "nope:x", "dst": "fault:overspeed"}).json()
    assert miss["found"] is False and miss["edges"] == []


@NEEDS_UPSTREAM
def test_contract_kb_stats_symptom_causal(client):
    """kb/stats 契约：symptom_causal 台账完整（12/41/13/54）。"""
    s = client.get("/api/kb/stats").json()
    assert "graph" in s and "vector" in s and "domain_enrichment" in s and "symptom_causal" in s
    sc = s["symptom_causal"]
    assert sc["symptoms"] == 12 and sc["indicates"] == 41 and sc["causes"] == 13
    assert sc["total_edges"] == 54
    assert s["graph"]["by_kind"]["symptom"] == 12
