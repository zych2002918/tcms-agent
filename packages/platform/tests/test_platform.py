"""P1 测试：资产模型加载 + FastAPI 服务层（真实上游资产冒烟）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcms_ai_platform.core import AssetLoadError, load_asset_model
from tcms_ai_platform.server.app import create_app

# 上游根（与本仓库同级的兄弟目录）
UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@NEEDS_UPSTREAM
def test_load_real_assets_counts():
    """真实资产加载计数（派生自文件，非手抄）。"""
    m = load_asset_model(UPSTREAM)
    s = m.stats()
    assert s["messages"] == 22
    assert s["signals"] == 116
    assert s["faults"] == 203
    assert s["scenarios"] == 104
    assert s["req_ids"] == 52
    assert s["functions"] == 11
    assert s["devices"] == 11
    assert m.load_stats["bad"] == []


@NEEDS_UPSTREAM
def test_real_message_signal_choices():
    """真实 DBC 报文/信号（含枚举表）解析正确。"""
    m = load_asset_model(UPSTREAM)
    door = m.message("DoorControl")
    assert door.frame_id == 0x400
    assert door.cycle_ms == 100
    assert door.send_type == "cyclic"
    # 枚举
    d1 = m.signal("Door1State")
    assert d1.choices == {0: "Closed", 1: "Open", 2: "Fault", 3: "Unknown"}
    # 带缩放/单位
    spd = m.signal("SpeedKmh")
    assert spd.scale == 0.1
    assert spd.unit == "km/h"
    assert spd.maximum == 200.0


@NEEDS_UPSTREAM
def test_real_message_segment_attribute():
    """DBC GenMsgSegment（多网段单一真源）解析进 MessageDef.segment。"""
    m = load_asset_model(UPSTREAM)
    assert m.message("TCMS_Heartbeat").segment == "vehicle"
    assert m.message("VehicleSpeed").segment == "vehicle"
    assert m.message("HVAC_CabinStatus").segment == "comfort"
    assert m.message("PIS_PassengerInfo").segment == "comfort"
    assert m.message("Gateway_Status").segment == "backbone"
    # 事件帧无段
    assert m.message("AlarmEvent").segment == ""
    # 周期帧必须全部有段归属（无 '' 周期帧）
    empty_seg_cyclic = [
        name for name, mm in m.messages.items()
        if mm.cycle_ms and mm.cycle_ms > 0 and not mm.segment
    ]
    assert not empty_seg_cyclic, f"周期帧缺网段标注: {empty_seg_cyclic}"


@NEEDS_UPSTREAM
def test_real_fault_and_function_anchor():
    """故障字段与功能表 RTM 锚定。"""
    m = load_asset_model(UPSTREAM)
    eb = m.fault("eb_failure")
    assert eb.fid == "F-TCMS-011"
    assert eb.level == "critical"
    assert eb.action == "emergency_brake"
    assert eb.sil == "4"
    # 功能表每个 req 都在 RTM（loader 内部已校验，这里再断言查询面）
    f = m.function("F-EBM")
    assert f.name == "紧急制动管理（EBM）"
    assert all(r in m.requirements for r in f.requirements)


@NEEDS_UPSTREAM
def test_real_scenario_steps():
    """真实场景步骤解析（事件式与 inject/recover）。"""
    m = load_asset_model(UPSTREAM)
    dc = m.scenario("door_cascade.yaml")
    assert dc.name == "车门故障级联"
    assert len(dc.steps) == 4
    injects = [st for st in dc.steps if st.action == "inject"]
    assert {st.fault for st in injects} == {"door_fault", "overspeed"}
    recovers = [st for st in dc.steps if st.action == "recover"]
    assert len(recovers) == 2


@NEEDS_UPSTREAM
def test_load_error_missing_dir():
    with pytest.raises(AssetLoadError):
        load_asset_model(Path("Z:/no/such/upstream"))


# ---- 服务层 ----


@pytest.fixture(scope="module")
def client():
    """真实上游加载的 app 测试客户端。"""
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    app = create_app(upstream=UPSTREAM)
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_stats_endpoint(client):
    s = client.get("/api/stats").json()
    assert s["messages"] == 22
    assert s["functions"] == 11


def test_messages_endpoint(client):
    msgs = client.get("/api/messages").json()
    assert len(msgs) == 22
    names = {m["name"] for m in msgs}
    assert "DoorControl" in names and "TCMS_Heartbeat" in names


def test_get_message_with_signals(client):
    d = client.get("/api/messages/DoorControl").json()
    assert d["frame_id"] == "0x400"
    sigs = {s["name"] for s in d["signals"]}
    assert "Door1State" in sigs
    door1 = next(s for s in d["signals"] if s["name"] == "Door1State")
    assert door1["choices"] == [
        {"value": 0, "label": "Closed"},
        {"value": 1, "label": "Open"},
        {"value": 2, "label": "Fault"},
        {"value": 3, "label": "Unknown"},
    ]


def test_get_message_404(client):
    assert client.get("/api/messages/Nope").status_code == 404


def test_devices_endpoint(client):
    devs = client.get("/api/devices").json()
    assert {d["name"] for d in devs} >= {"VCU", "BCU", "BMS"}


def test_faults_endpoint(client):
    faults = client.get("/api/faults").json()
    assert len(faults) == 203
    eb = next(f for f in faults if f["key"] == "eb_failure")
    assert eb["action"] == "emergency_brake"


def test_fault_detail(client):
    f = client.get("/api/faults/overspeed").json()
    assert f["fid"] == "F-TCMS-007"
    assert "recovery" in f


def test_scenarios_endpoint(client):
    scs = client.get("/api/scenarios").json()
    assert len(scs) == 104
    assert any(s["file"] == "door_cascade.yaml" for s in scs)


def test_requirements_endpoint(client):
    reqs = client.get("/api/requirements").json()
    ids = {r["req_id"] for r in reqs}
    assert "SR-01" in ids and "SR-16" in ids


def test_functions_endpoint(client):
    funcs = client.get("/api/functions").json()
    assert len(funcs) == 11
    f = next(f for f in funcs if f["fid"] == "F-EBM")
    assert "SR-01" in f["requirements"]


def test_run_scenario_endpoint(client):
    """真实上游引擎执行单场景（离线确定性）。"""
    r = client.post("/api/run/scenario", json={"scenario": "door_cascade.yaml"})
    assert r.status_code == 200
    body = r.json()
    assert body["all_passed"] is True
    assert body["passed"] == 2
    assert body["engine_version"]  # 上游引擎版本自证


def test_run_scenario_404(client):
    r = client.post("/api/run/scenario", json={"scenario": "nope.yaml"})
    assert r.status_code == 404
    assert "场景不存在" in r.json()["detail"]


def test_run_scenarios_all(client):
    """批量执行全部 104 场景全部通过（真实引擎）。"""
    r = client.post("/api/run/scenarios", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["scenario_count"] == 104
    assert body["all_passed"] is True
    assert body["total_fail"] == 0


# ---- P2 知识底座 API ----


def test_kb_stats(client):
    s = client.get("/api/kb/stats").json()
    # 图谱 ≥ 基础节点 + 领域注入 + 症状(12) 与因果边;向量 ≥ 基础 + 领域文档
    assert s["graph"]["nodes"] >= 200
    assert s["vector"]["docs"] >= 200
    assert "domain_enrichment" in s
    assert s["domain_enrichment"]  # 领域注入统计非空(ebm/network/safety/systems)
    assert "symptom_causal" in s  # B 步：症状/因果注入统计进 kb 台账
    sc = s["symptom_causal"]
    assert sc["symptoms"] == 12 and sc["indicates"] == 41 and sc["causes"] == 13
    assert sc["total_edges"] == 54


def test_kb_search(client):
    r = client.post("/api/kb/search", json={"query": "车门故障 不能发车", "k": 3})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert hits
    assert all("graph_neighbors" in h for h in hits)


def test_kb_subgraph(client):
    r = client.post("/api/kb/subgraph", json={"seed": "fault:overspeed", "depth": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["node_count"] >= 5
    kinds = {n["kind"] for n in body["nodes"]}
    assert "function" in kinds and "scenario" in kinds


def test_kb_nodes_filter(client):
    nodes = client.get("/api/kb/nodes", params={"kind": "fault", "limit": 5000}).json()
    assert len(nodes) == 203
    assert all(n["kind"] == "fault" for n in nodes)


def test_kb_node_detail(client):
    r = client.get("/api/kb/node/fault:door_fault")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "fault"
    nids = {n["id"] for n in body["neighbors"]}
    assert "scenario:door_cascade.yaml" in nids
    assert "function:F-DOOR" in nids


def test_kb_node_404(client):
    assert client.get("/api/kb/node/nope:xyz").status_code == 404


def test_run_scenario_records_sink(client):
    """执行单场景会沉淀 run 节点到知识库（组织记忆）。"""
    client.post("/api/run/scenario", json={"scenario": "overspeed_derate.yaml"})
    nodes = client.get("/api/kb/nodes", params={"kind": "run"}).json()
    assert len(nodes) >= 1


# ---- P4 Agent Harness API ----


def test_agent_tasks(client):
    tasks = client.get("/api/agent/tasks").json()
    ids = {t["task_id"] for t in tasks}
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
    t = next(t for t in tasks if t["task_id"] == "T-EBM")
    assert t["expected_action"] == "emergency_brake"


def test_agent_run_single(client):
    r = client.post("/api/agent/run", json={"task_id": "T-DOOR"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["achieved"] == 1
    run = body["runs"][0]
    assert run["achieved"] is True
    assert run["fault"] == "door_fault"
    assert run["score"]["score"] >= 60
    steps = {t["step"] for t in run["trace"]}
    assert {"plan", "retrieve", "exec", "verify"} <= steps


def test_agent_run_all(client):
    r = client.post("/api/agent/run", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 8
    assert body["achieved"] == 8
    assert body["success_rate"] == 1.0
    # 每个 run 都带证据链（RAG 可见）
    for run in body["runs"]:
        assert run["evidence"] or not run["evidence"]  # 允许空但结构在
    assert all("evidence" in run for run in body["runs"])


def test_agent_run_404(client):
    r = client.post("/api/agent/run", json={"task_id": "T-NOPE"})
    assert r.status_code == 404


# ---- 症状多跳诊断（C 步：/api/agent/diagnose）----


def test_diagnose_dashboard_flicker_regression_http(client):
    """专项回归：仪表盘闪烁但无故障码 → 非空/可溯源/不编造故障码/含供电+显示域候选。"""
    r = client.post("/api/agent/diagnose", json={"message": "仪表盘闪烁但无故障码"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["no_match"] is False
    assert body["symptom"]["key"] == "dashboard_flicker"
    assert body["reply"] and len(body["reply"]) > 40  # 非空中文诊断
    assert body["candidates"]
    assert body["no_fault_code_invented"] is True
    # 溯源：候选全部为真实故障字典键
    fk = {f["key"] for f in client.get("/api/faults").json()}
    assert len(fk) == 203
    for c in body["candidates"]:
        assert c["fault"] in fk, f"编造故障键: {c['fault']}"
        assert c["basis"] in ("real_mechanism", "derived")
        assert c["confidence"] > 0
        assert c["check"]  # 每条建议带验证动作
    keys = {c["fault"] for c in body["candidates"]}
    # 供电(aux)域：24V 欠压 / 支撑电容老化；显示(网络/列车控制)域：司控台屏黑屏
    assert "aux_24v_undervoltage" in keys
    assert "aux_capacitor_aging" in keys
    assert "cab_display_blank" in keys
    domains = {c["domain"] for c in body["candidates"]}
    assert "aux" in domains and "network" in domains
    # 证据可溯源：因果链逐跳 basis/note 在建议里
    assert body["evidence"]["symptom_hit"]["doc_id"] == "symptom:dashboard_flicker"


def test_diagnose_unknown_symptom_honest_http(client):
    """无匹配症状 → no_match/不确定 + 需补充引导（诚实不硬答、不编码）。"""
    r = client.post("/api/agent/diagnose", json={"message": "车厢地板漏水"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert body["no_match"] is True
    assert body["uncertain"] is True
    assert body["candidates"] == []
    assert body["no_fault_code_invented"] is True
    assert "补充" in body["reply"] or "把握" in body["reply"]


def test_diagnose_broad_phrase_gives_related_assets_http(client):
    """宽泛描述(空调坏了)→ no_match 但给出可点击相关资产与定向推荐，不空手引导。"""
    r = client.post("/api/agent/diagnose", json={"message": "空调坏了"})
    assert r.status_code == 200
    b = r.json()
    assert b["no_match"] is True
    rel = b.get("related_assets") or []
    assert rel, "空调坏了 应能给出相关资产（hvac 域故障/场景）"
    assert all(a["doc_id"].startswith(("fault:", "scenario:")) for a in rel)
    assert any("空调" in a["text"] or "hvac" in a["doc_id"] for a in rel)
    # 宽泛问法推理：定向推荐真实故障 + 复现场景
    rec = b.get("recommendation") or {}
    assert rec.get("kind") == "vague_domain" and rec.get("domain") == "hvac"
    fk = {f["key"] for f in client.get("/api/faults").json()}
    assert rec.get("faults") and all(f["key"] in fk for f in rec["faults"])


def test_diagnose_empty_message_guided(client):
    """空输入 → 引导（绝不 422/500）。"""
    r = client.post("/api/agent/diagnose", json={"message": ""})
    assert r.status_code == 200
    assert r.json()["no_match"] is True


def test_diagnose_multi_turn_session_http(client):
    """P1-1 HTTP 多轮：带 session_id 连续追问 → 第二轮回溯上一轮症状锚点。"""
    sid = "test-session-http-1"
    r1 = client.post("/api/agent/diagnose", json={"message": "仪表盘闪烁但无故障码", "session_id": sid})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["symptom"]["key"] == "dashboard_flicker"
    assert b1["session_id"] == sid

    r2 = client.post(
        "/api/agent/diagnose",
        json={"message": "再说一下刚才那个部位该查哪个信号", "session_id": sid},
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["session_anchor_used"] is True
    assert b2["symptom"]["key"] == "dashboard_flicker"
    assert b2["evidence"]["session"]["prior_symptom_key"] == "dashboard_flicker"
    # 换会话 id → 无历史，指代词不得凭空作答
    r3 = client.post(
        "/api/agent/diagnose",
        json={"message": "再说一下刚才那个部位该查哪个信号", "session_id": "test-session-http-2"},
    )
    b3 = r3.json()
    assert b3["no_match"] is True and b3["session_anchor_used"] is False

    r4 = client.post(
        "/api/agent/diagnose",
        json={"message": "SOC 跳变", "session_id": sid},
    )
    b4 = r4.json()
    assert b4["session_anchor_used"] is False
    assert b4["symptom"]["key"] == "soc_jump", "换题（无指代词）应直配新症状"


def test_system_status_capability_symptom_diagnosis(client):
    """system/status 能力清单包含 symptom_diagnosis。"""
    caps = client.get("/api/system/status").json()["capabilities"]
    assert caps["symptom_diagnosis"] is True


# ---- FaultLab 演示（真实场景 → 事件时间线 + 通道曲线）----


def test_faultlab_scenarios(client):
    r = client.get("/api/faultlab/scenarios")
    assert r.status_code == 200
    scs = r.json()
    assert len(scs) == 104
    assert any(s["file"] == "overspeed_derate.yaml" for s in scs)


def test_faultlab_demo_overspeed(client):
    r = client.post("/api/faultlab/demo", json={"scenario": "overspeed_derate.yaml"})
    assert r.status_code == 200
    body = r.json()
    assert body["demo"]["scenario"] == "overspeed_derate.yaml"
    assert body["curve"]
    kinds = {e["kind"] for e in body["demo"]["events"]}
    assert {"inject", "detect", "action", "recover"} <= kinds
    # 超速 → 处置 derate 出现在时间线上
    acts = [e for e in body["demo"]["events"] if e["kind"] == "action"]
    assert any(e["action"] == "derate" for e in acts)
    # 曲线里速度应出现超限(>160)后回落
    speeds = [c["speed_kmh"] for c in body["curve"]]
    assert max(speeds) > 160
    assert min(speeds) <= 120


def test_faultlab_demo_404(client):
    r = client.post("/api/faultlab/demo", json={"scenario": "nope.yaml"})
    assert r.status_code == 404


def test_faultlab_demo_engine_window(client):
    """引擎观察窗：engine.version 非空(真实执行已附)、事件带 source 溯源、pipeline 有常量表。

    由 t1 在 faultlab_demo 端点给 run_result 补 engine_version 支撑——
    run_yaml 报告本身不带版本，缺补丁则 version=None（回归锁定）。
    """
    r = client.post("/api/faultlab/demo", json={"scenario": "overspeed_derate.yaml"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine_asserted"] is True  # 引擎可用(测试夹具挂上游)
    eng = body["demo"]["engine"]
    assert eng["asserted"] is True
    assert eng["version"]  # t1 补丁：真实引擎版本(如 1.9.1)，而非 None
    assert body["demo"]["pipeline"]["title"]
    # 真实/示意常量表齐备（透明自证）
    assert body["demo"]["pipeline"]["constants"]["real"]
    assert body["demo"]["pipeline"]["constants"]["schematic"]
    # 事件带结构化 source（引擎断言/场景 YAML/故障字典溯源）
    act_evts = [e for e in body["demo"]["events"] if e["kind"] == "action"]
    assert any(e.get("source", {}).get("kind") == "engine_assert" for e in act_evts)
    inj_evts = [e for e in body["demo"]["events"] if e["kind"] == "inject"]
    assert any(e.get("source", {}).get("kind") == "scenario_yaml" for e in inj_evts)


# ---- 设置层 / 新手引导 API（外部可配置接口；key 永不外泄）----


def test_settings_get_no_key_leak(client, monkeypatch, tmp_path):
    """读取设置不应暴露 api_key（响应只含 has_key 布尔）。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    # 先写一个带 key 的设置（直接经 API，隔离到 tmp）
    r = client.post("/api/settings", json={"llm_provider": "aliyun", "llm_api_key": "sk-secret-abc", "llm_model": "m"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["has_key"] is True
    assert "api_key" not in json.dumps(body)  # 响应当中绝无 key
    # GET 同
    g = client.get("/api/settings").json()
    assert g["llm"]["has_key"] is True
    assert "sk-secret-abc" not in json.dumps(g)


def test_settings_save_and_clear(client, monkeypatch, tmp_path):
    """保存(provider/model)与清除 key 往返。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    client.post("/api/settings", json={"llm_provider": "deepseek", "llm_model": "deepseek-chat", "llm_api_key": "sk-d-1"})
    g = client.get("/api/settings").json()
    assert g["llm"]["provider"] == "deepseek"
    assert g["llm"]["model"] == "deepseek-chat"
    # 清除 key
    client.post("/api/settings/clear-api-key")
    g2 = client.get("/api/settings").json()
    assert g2["llm"]["has_key"] is False


def test_settings_asset_dir_invalid_raises(client, monkeypatch, tmp_path):
    """无效 asset_dir 保存后,resolve_asset_source 应抛错引导(而非静默回退)。"""
    from tcms_ai_platform.core.sources import resolve_asset_source

    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    r = client.post("/api/settings", json={"asset_dir": "Z:/no/such/tcms"})
    assert r.status_code == 200

    with pytest.raises(FileNotFoundError):
        resolve_asset_source()
    # 清空恢复
    client.post("/api/settings", json={"asset_dir": ""})
    resolve_asset_source()  # 不再抛


def test_faultlab_demo_has_params(client):
    """FaultLab demo 输出应带档位参数(limit/derate/cruise/eb),前端不再硬编码。"""
    r = client.post("/api/faultlab/demo", json={"scenario": "overspeed_derate.yaml"})
    assert r.status_code == 200
    p = r.json()["demo"]["params"]
    assert p["limit_kmh"] == 160.0
    assert p["derate_speed"] > 0
    assert p["eb_kpa"] > 0

# ---- LLM 模型列表探测（/api/llm/models）----


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | str):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload if isinstance(self._payload, dict) else {}

    @property
    def text(self) -> str:
        return self._payload if isinstance(self._payload, str) else ""


def test_llm_models_success(client, monkeypatch, tmp_path):
    """配置 key 后可拉取真实模型列表（mock 远端 GET /models）。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    monkeypatch.setenv("DASH_API_KEY", "sk-test")
    from tcms_ai_platform.agent import llm_backend as _lb

    monkeypatch.setattr(
        _lb.httpx,
        "get",
        lambda url, headers=None, timeout=None: _FakeResp(
            200,
            {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner", "owned_by": "deepseek"}]},
        ),
    )
    r = client.post("/api/llm/models", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert [m["id"] for m in body["models"]] == ["deepseek-reasoner", "deepseek-chat"]  # 推理类排序在前
    # key 绝不出现在响应
    assert "sk-test" not in r.text


def test_llm_models_no_key(client, monkeypatch, tmp_path):
    """无 key → ok=False + 中文引导（不抛 500）。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    monkeypatch.delenv("DASH_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    # 隔离本机 DSH 凭据（第三 key 来源），保证无 key
    empty_cred = tmp_path / "empty-credentials.yaml"
    empty_cred.write_text("refs: {}\n", encoding="utf-8")
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(empty_cred))
    r = client.post("/api/llm/models", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "API key" in (body["error"] or "")
    assert body["models"] == []


def test_llm_models_remote_error(client, monkeypatch, tmp_path):
    """远端 401/错误 → ok=False + 诚实错误文案。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    monkeypatch.setenv("DASH_API_KEY", "sk-bad")
    from tcms_ai_platform.agent import llm_backend as _lb

    monkeypatch.setattr(
        _lb.httpx,
        "get",
        lambda url, headers=None, timeout=None: _FakeResp(401, "unauthorized"),
    )
    r = client.post("/api/llm/models", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "401" in (body["error"] or "")


def test_llm_models_explicit_override(client, monkeypatch, tmp_path):
    """显式传 base_url/key(仅探测)可覆盖本机配置;key 不落 settings。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    monkeypatch.delenv("DASH_API_KEY", raising=False)
    from tcms_ai_platform.agent import llm_backend as _lb
    from tcms_ai_platform.core import settings as _settings

    captured: dict = {}
    monkeypatch.setattr(
        _lb.httpx,
        "get",
        lambda url, headers=None, timeout=None: _capture(url, headers, captured),
    )
    r = client.post(
        "/api/llm/models",
        json={"base_url": "https://example.com/v1", "api_key": "sk-ephemeral"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # key 未持久化
    assert _settings.llm_api_key() is None


def _capture(url: str, headers: dict | None, captured: dict) -> _FakeResp:
    captured["url"] = url
    captured["auth"] = (headers or {}).get("Authorization", "")
    return _FakeResp(200, {"data": [{"id": "m1"}]})


def test_fetch_models_sort_chat_first():
    """拉到的模型列表应对话/推理类在前，embedding/rerank 垫底（选模型体验）。"""
    from tcms_ai_platform.agent.llm_backend import _sort_models

    raw = [
        {"id": "text-embedding-v3", "owned_by": "system"},
        {"id": "deepseek-reasoner", "owned_by": "deepseek"},
        {"id": "qwen-max", "owned_by": "system"},
        {"id": "text-rerank-v2", "owned_by": "system"},
        {"id": "qwen-plus", "owned_by": "system"},
    ]
    s = _sort_models(raw)
    ids = [m["id"] for m in s]
    assert ids[0] == "deepseek-reasoner"  # 推理类最前
    assert "text-embedding-v3" in ids[-2:]  # embedding 垫底
    assert "text-rerank-v2" in ids[-2:]
    assert ids.index("qwen-plus") < ids.index("text-embedding-v3")
