"""白盒剖析 + 多故障解释：机器化守卫。

本文件守护两件事，都是真实使用中暴露出来的：

**A. Agent 运行期间究竟展示了什么。**
此前运行中只有一段**硬编码轮换文案**（"正在检索证据…"），那是进度动画不是白盒。
现在后端为每条轨迹附带结构化载荷（真实查询串、完整候选集与选择理由、
该场景实际注入了哪些故障、断言逐条明细），并提供 SSE 流式端点。
这里断言：流按序发出 start→trace*→result→end；每条 trace 带载荷；
末尾的 result 与 `POST /api/agent/run` **同构**（否则前端就得把任务跑两遍）。

**B. "点一个故障却有多个被注入"是不是多余注入。**
事实是：它们是同一个场景里**并列编排**的步骤，没有前置依赖。
全库 104 个场景里 61 个是多故障（最多 6 个），所以解释必须是通用的。
这里用一条**全库不变式**守住：每个场景的构成数必须等于它实际的 inject 步数——
一旦有人改了场景或改了统计口径，两边对不上就红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.core.scenario_view import (
    scenario_composition,
    scenario_injections,
)
from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"  # monorepo: packages/engine
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

#: 用户实际踩到的那个场景：从一个故障点进去，看到三个故障被注入
CASE_SCENARIO = "soc_temp_door_noise.yaml"
CASE_ENTRY = "door_sensor_noise"  # 车门传感器偶发噪声


@pytest.fixture(scope="module")
def model():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    return load_asset_model(UPSTREAM)


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    return TestClient(create_app(upstream=UPSTREAM))


# ---------------------------------------------------------------------------
# A. 场景构成：事实摊开
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_the_actual_case_has_three_faults(model):
    """用户踩到的那个场景：确实注入 3 个故障，且入口故障是其中之一。"""
    sc = model.scenarios[CASE_SCENARIO]
    inj = scenario_injections(sc)
    assert len(inj) == 3, [i["fault"] for i in inj]
    assert [i["fault"] for i in inj] == ["soc_low", "temp_high", "door_sensor_noise"]
    # 按注入时刻排序
    assert [i["at"] for i in inj] == sorted(i["at"] for i in inj)


@NEEDS_UPSTREAM
def test_composition_marks_entry_vs_co_injected(model):
    """入口故障与"同场景一并注入"必须被区分——这正是用户困惑的那句话。"""
    comp = scenario_composition(model.scenarios[CASE_SCENARIO], model, CASE_ENTRY)
    assert comp["available"] and comp["is_multi"] and comp["total_faults"] == 3
    assert comp["has_entry"] and comp["entry_fault"] == CASE_ENTRY

    roles = {i["fault"]: i["role"] for i in comp["injections"]}
    assert roles[CASE_ENTRY] == "entry"
    assert roles["soc_low"] == "co" and roles["temp_high"] == "co"

    entry_item = next(i for i in comp["injections"] if i["role"] == "entry")
    assert entry_item["fault_name"] == "车门传感器偶发噪声"
    assert entry_item["level_label"] == "轻微"
    assert entry_item["expect_label"] == "告警"

    co_names = {i["fault_name"] for i in comp["injections"] if i["role"] == "co"}
    assert co_names == {"SOC 偏低", "电池温度偏高"}


@NEEDS_UPSTREAM
def test_why_states_no_precondition_dependency(model):
    """结论必须直说"不是前置条件"——这是用户问题的答案本身。"""
    comp = scenario_composition(model.scenarios[CASE_SCENARIO], model, CASE_ENTRY)
    assert "前置条件" in comp["why"] or "前置依赖" in comp["why"]
    assert "并列" in comp["why"]
    # 面向演示的一句话口径要可直接照读：含全部故障与各自的等级/期望
    assert "SOC 偏低" in comp["oneliner"] and "车门传感器偶发噪声" in comp["oneliner"]
    assert comp["oneliner"].count("；") == 2  # 三个故障用分号连接


@NEEDS_UPSTREAM
def test_explanatory_text_has_no_markdown_markers(model):
    """说明文字会原样渲染在网页上，不能夹带 markdown 星号（会显示成字面量）。"""
    for name, sc in model.scenarios.items():
        comp = scenario_composition(sc, model)
        for field in ("why", "oneliner"):
            assert "**" not in comp[field], f"{name} 的 {field} 含 markdown 标记"


@NEEDS_UPSTREAM
def test_single_fault_scenario_is_not_flagged_multi(model):
    """只有一个不同故障的场景不该按"多故障"弹解释卡（否则提示会变成噪音）。

    注意"只有一个不同故障"包含两种情况：真·单次注入，以及"恢复后重发"
    （注入多次但仍是同一个故障）。两者都 `is_multi is False`，
    但 `relation` 不同 —— 措辞必须区分，否则会在重发场景上说错话。
    """
    seen_relations = set()
    for c in (scenario_composition(s, model) for s in model.scenarios.values()):
        if c["total_faults"] != 1:
            continue
        assert c["is_multi"] is False
        assert c["relation"] in ("single", "repeat_single")
        seen_relations.add(c["relation"])
    assert "single" in seen_relations, "语料里应有真·单故障场景"


@NEEDS_UPSTREAM
def test_library_wide_invariant_composition_matches_scenario_steps(model):
    """**全库不变式**：构成统计必须与场景 YAML 的 inject/recover 步数逐项一致。

    这条是本文件的重点：多故障解释是对**所有**场景生效的通用机制，
    因此"统计口径"与"场景事实"必须处处一致，不能只对那一个案例成立。

    注意两个数要分开断言（本测试第一版把它们混为一谈，因此抓出了真缺陷）：
    - `total_injections` == inject 步数（含重复注入）
    - `total_faults` == **去重后**的故障数 == `fault_keys` 大小
    """
    multi = 0
    repeat = 0
    for name, sc in model.scenarios.items():
        comp = scenario_composition(sc, model)
        raw_inject = [s for s in sc.steps if s.action == "inject"]
        raw_recover = [s for s in sc.steps if s.action == "recover"]

        assert comp["total_injections"] == len(raw_inject), f"{name} 注入次数不符"
        assert comp["total_faults"] == len({s.fault for s in raw_inject}), f"{name} 去重故障数不符"
        assert comp["total_faults"] == len(sc.fault_keys), f"{name} 与 fault_keys 不符"
        assert len(comp["injections"]) == len(raw_inject), f"{name} 明细条数不符"
        assert len(comp["recoveries"]) == len(raw_recover), f"{name} 恢复数不符"

        if comp["total_injections"] != comp["total_faults"]:
            repeat += 1
            assert comp["repeated"], f"{name} 次数与故障数不等却没有 repeated 明细"
        if comp["total_faults"] > 1:
            multi += 1
            assert comp["is_multi"] is True
    assert multi >= 50, f"多故障场景应占多数（实测 61/104），实际 {multi}"
    assert repeat >= 1, "语料里应有「恢复后重发」型场景（这正是两个数会不等的地方）"


@NEEDS_UPSTREAM
def test_repeat_injection_scenario_is_described_accurately(model):
    """恢复后重发：只涉及 1 个故障但注入多次 —— 措辞不能说成"多个故障"。

    这是上面那条不变式测试抓出来的真实缺陷：把"注入次数"当"故障个数"，
    在重发型场景上就会说错话。
    """
    sc = model.scenarios["overspeed_reinject_repeat.yaml"]
    comp = scenario_composition(sc, model, "overspeed")
    assert comp["total_injections"] == 2 and comp["total_faults"] == 1
    assert comp["is_multi"] is False, "只有一个不同故障，不该按多故障弹解释卡"
    assert comp["relation"] == "repeat_single"
    assert "重发" in comp["why"] and "并不是有多个不同的故障" in comp["why"]
    assert comp["repeated"] and comp["repeated"][0]["times"] == 2
    assert "2 次" in comp["oneliner"]


@NEEDS_UPSTREAM
def test_composition_endpoint_shape_and_404(client):
    r = client.get(f"/api/scenarios/{CASE_SCENARIO}/composition", params={"entry_fault": CASE_ENTRY})
    assert r.status_code == 200
    d = r.json()
    for k in ("available", "total_faults", "injections", "why", "oneliner", "is_multi", "recoveries"):
        assert k in d, f"缺少字段 {k}"
    assert d["total_faults"] == 3
    assert client.get("/api/scenarios/nope.yaml/composition").status_code == 404


# ---------------------------------------------------------------------------
# B. Agent 白盒流：真实步骤 + 结构化载荷
# ---------------------------------------------------------------------------


def _read_stream(client, **params) -> list[dict]:
    import json

    events: list[dict] = []
    with client.stream("GET", "/api/agent/run/stream", params=params) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@NEEDS_UPSTREAM
def test_stream_event_sequence(client):
    """流的骨架：start → trace* → result → end，且类型都在约定集合内。"""
    evs = _read_stream(client, task_id="T-DOOR")
    types = [e["type"] for e in evs]
    assert types[0] == "start"
    assert types[-1] == "end"
    assert "result" in types
    assert types.count("trace") >= 3, types
    assert set(types) <= {"start", "trace", "done", "result", "error", "end"}, set(types)
    assert "error" not in types, [e for e in evs if e["type"] == "error"]
    # start 要说明跑哪几条任务
    assert evs[0]["tasks"] and evs[0]["count"] == len(evs[0]["tasks"])


@NEEDS_UPSTREAM
def test_stream_trace_carries_whitebox_payload(client):
    """每条关键轨迹都必须带结构化载荷——白盒的"料"就在这里。"""
    evs = _read_stream(client, task_id="T-DOOR")
    traces = [e["entry"] for e in evs if e["type"] == "trace"]
    by_step: dict[str, list[dict]] = {}
    for t in traces:
        by_step.setdefault(t["step"], []).append(t)

    for step in ("plan", "retrieve", "act", "exec", "verify"):
        assert step in by_step, f"轨迹里缺少 {step} 步：{list(by_step)}"
        assert by_step[step][0].get("payload"), f"{step} 步没有载荷"

    # retrieve：必须给出**真实查询串**与逐条命中（此前只有一句"命中 N 条"）
    rp = by_step["retrieve"][0]["payload"]
    assert rp.get("query"), rp
    assert isinstance(rp.get("hits"), list) and rp["hits"]
    assert {"doc_id", "score"} <= set(rp["hits"][0])

    # act：必须给出**完整候选集**与选中标记（白盒要的是选择空间，不只是结果）
    ap = by_step["act"][0]["payload"]
    assert ap.get("chosen_scenario") and ap.get("strategy")
    assert isinstance(ap.get("candidates"), list)
    assert any(c.get("is_chosen") for c in ap["candidates"]), ap["candidates"]


@NEEDS_UPSTREAM
def test_exec_payload_discloses_injected_faults(client, model):
    """`exec` 步必须摊开该场景**实际注入了哪些故障**——这是最容易被问住的地方。

    断言载荷与场景 YAML 逐键一致（不是转述，是可核对的事实）。
    """
    evs = _read_stream(client, task_id="T-DOOR")
    execs = [e["entry"]["payload"] for e in evs if e["type"] == "trace" and e["entry"]["step"] == "exec"]
    assert execs, "应有 exec 步"
    p = execs[0]
    assert p["scenario"] and p["injected_count"] == len(p["injected"])
    sc = model.scenarios[p["scenario"]]
    yaml_keys = [s.fault for s in sc.steps if s.action == "inject"]
    assert [i["fault"] for i in p["injected"]] == yaml_keys, (
        f"载荷里的注入故障与 {p['scenario']} 的 YAML 不一致"
    )
    # 每个故障还要带上等级/期望/现象，供前端直接渲染
    for i in p["injected"]:
        assert "level" in i and "expect" in i and "at" in i


@NEEDS_UPSTREAM
def test_verify_payload_has_assertion_detail(client):
    """断言核对必须给到 expect→actual 逐条明细，而不是只报一个 achieved。"""
    evs = _read_stream(client, task_id="T-DOOR")
    vp = next(e["entry"]["payload"] for e in evs if e["type"] == "trace" and e["entry"]["step"] == "verify")
    assert "achieved" in vp and "relevant" in vp
    assert vp["relevant"], "应至少有一条相关断言"
    a = vp["relevant"][0]
    assert {"fault", "expect", "actual", "passed"} <= set(a)


@NEEDS_UPSTREAM
def test_stream_result_is_isomorphic_with_agent_run(client):
    """流末尾的 result 必须与 `POST /api/agent/run` 同构。

    否则前端要么把任务跑两遍（浪费一次真实引擎执行 + 一次模型调用），
    要么流和报告两套结构各写一份渲染——两条路都不可接受。
    """
    evs = _read_stream(client, task_id="T-DOOR")
    rep = next(e["report"] for e in evs if e["type"] == "result")
    sync = client.post("/api/agent/run", json={"task_id": "T-DOOR"}).json()
    assert set(rep.keys()) == set(sync.keys()), (sorted(rep), sorted(sync))
    assert set(rep["runs"][0].keys()) == set(sync["runs"][0].keys())
    assert rep["runs"][0]["task_id"] == sync["runs"][0]["task_id"]
    # 轨迹在流里是增量推的，在报告里是完整的；两边步名应一致
    assert [t["step"] for t in rep["runs"][0]["trace"]] == [t["step"] for t in sync["runs"][0]["trace"]]


@NEEDS_UPSTREAM
def test_stream_unknown_task_is_404(client):
    assert client.get("/api/agent/run/stream", params={"task_id": "NOPE"}).status_code == 404


@NEEDS_UPSTREAM
def test_stream_failure_does_not_break_the_task(model):
    """回调抛异常绝不能影响正跑的任务（推送失败只是看不到流，不是任务失败）。"""
    from tcms_ai_platform.agent.harness import AgentHarness, MockAgentBackend
    from tcms_ai_platform.agent.tasks import default_tasks
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    # 必须与真实 app 同构：少了 enrich_graph 评审会判缺口，任务就不达成了
    # （这一点本身也说明评审确实在起作用，不是装饰）
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(model))
    g = build_knowledge_graph(model)
    enrich_graph(g, vs)

    def boom(_entry: dict) -> None:
        raise RuntimeError("假装前端断线")

    h = AgentHarness(model, HybridRetriever(vs, g), UPSTREAM / "scenarios", backend=MockAgentBackend())
    task = next(t for t in default_tasks(model) if t.task_id == "T-DOOR")
    run = h.run_task(task, on_log=boom)
    assert run.trace, "轨迹仍应记录"
    assert run.achieved is True, "任务仍应达成"
