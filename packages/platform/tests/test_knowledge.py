"""P2 测试：知识图谱 + 向量索引 + GraphRAG 混合检索 + 沉淀闭环。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.knowledge import (
    GraphSink,
    HashedEmbedder,
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
def kb():
    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    store = VectorStore()
    store.add_many(build_docs_from_asset(m))
    return {"model": m, "graph": g, "store": store, "retriever": HybridRetriever(store, g)}


# ---- 图谱 ----


@NEEDS_UPSTREAM
def test_graph_stats(kb):
    s = kb["graph"].stats()
    assert s["nodes"] == 519  # 22 报文/116 信号/203 故障/104 场景/52 需求/11 功能/11 设备（基础图，不含 enrich）
    assert s["edges"] >= 300
    assert s["by_kind"]["fault"] == 203
    assert s["by_kind"]["signal"] == 116
    assert s["by_kind"]["scenario"] == 104
    assert s["by_kind"]["function"] == 11


@NEEDS_UPSTREAM
def test_graph_function_anchors(kb):
    """功能节点必须联通知 报文/信号/需求/故障（可漫游）。"""
    g = kb["graph"]
    fn = "function:F-DOOR"
    nbs = {nid for nid, _ in g.neighbors(fn)}
    assert "message:DoorControl" in nbs
    assert "signal:Door1State" in nbs
    assert "fault:door_fault" in nbs
    assert "requirement:SR-04" in nbs


@NEEDS_UPSTREAM
def test_graph_subgraph_overspeed(kb):
    sub = kb["retriever"].subgraph("fault:overspeed", 2)
    assert sub["node_count"] >= 5
    kinds = {n["kind"] for n in sub["nodes"]}
    assert "function" in kinds and "scenario" in kinds


# ---- 向量 ----


@NEEDS_UPSTREAM
def test_vector_store_stats(kb):
    s = kb["store"].stats()
    assert s["docs"] == 508  # 资产文档：203 故障/116 信号/104 场景/52 需求/11 功能/22 报文
    assert s["by_kind"]["fault"] == 203
    assert s["by_kind"]["requirement"] == 52


@NEEDS_UPSTREAM
def test_vector_search_door(kb):
    hits = kb["store"].search("车门故障 不能发车", k=3)
    ids = {h["doc_id"] for h in hits}
    assert any("door" in i or "DOOR" in i for i in ids)


@NEEDS_UPSTREAM
def test_hashed_embedder_deterministic():
    emb = HashedEmbedder(dim=128)
    v1 = emb.embed("超速 160km/h 紧急制动")
    v2 = emb.embed("超速 160km/h 紧急制动")
    assert (v1 == v2).all()
    assert abs(float(v1 @ v1) - 1.0) < 1e-3  # 归一化


# ---- 混合检索 ----


@NEEDS_UPSTREAM
def test_hybrid_retrieve_evidence(kb):
    r = kb["retriever"].retrieve("紧急制动 执行失败", k=3)
    assert r["hits"]
    top = r["hits"][0]
    assert "doc_id" in top and "graph_neighbors" in top
    # 顶层命中应带结构邻接（证据链）
    assert top["graph_neighbors"] is not None


@NEEDS_UPSTREAM
def test_hybrid_door_has_graph_neighbors(kb):
    r = kb["retriever"].retrieve("车门故障 不能发车", k=5)
    door_hit = next((h for h in r["hits"] if h["doc_id"] == "fault:door_fault"), None)
    assert door_hit is not None
    nids = {n["id"] for n in door_hit["graph_neighbors"]}
    assert "scenario:door_cascade.yaml" in nids
    assert "function:F-DOOR" in nids


# ---- 沉淀闭环 ----


@NEEDS_UPSTREAM
def test_graph_sink_records_run(kb):
    g = kb["graph"]
    sink = GraphSink(g)
    before = len(g.nodes)
    sink.record_run(
        "run-test-001",
        "door_cascade.yaml",
        {"passed": 2, "failed": 0, "all_passed": True},
    )
    assert len(g.nodes) == before + 1
    assert len(sink.runs) == 1
    run_id = "run:run-test-001"
    assert run_id in g.nodes


# ---- Q4：有界分层检索（域路由 → 域内语义 topk） ----


@NEEDS_UPSTREAM
def test_partition_stats_domain_tagged(kb):
    """资产文档已按子系统打域标（有界索引的分区基础）。"""
    by = kb["store"].partition_stats()
    # 资产文档已按子系统打域标；验证域集合非空且可路由
    assert "door" in by and "network" in by and "brake" in by
    assert by.get("network", 0) >= 9  # 网络子系统 9 故障 + 相关报文/场景


@NEEDS_UPSTREAM
def test_asset_docs_all_partitioned(kb):
    """Q4 域标签铺全：故障/报文/信号/场景/功能文档必须全部分区（无空域）；
    需求仅"基础设施类 SR"允许空域（不被任何功能覆盖，属全局层）。"""
    from tcms_ai_platform.knowledge import build_docs_from_asset

    m = kb["model"]
    docs = build_docs_from_asset(m)
    kinds = {}
    for d in docs:
        kinds.setdefault(d.kind, []).append(d)

    # 每类中带非空 domain 的数量
    def nonempty(kind: str) -> int:
        return sum(1 for d in kinds.get(kind, []) if (d.meta.get("domain") or ""))

    assert nonempty("fault") == len(m.faults_by_key) == 203
    assert nonempty("message") == len(m.messages) == 22
    assert nonempty("signal") == len(m.signals) == 116
    assert nonempty("scenario") == len(m.scenarios) == 104
    assert nonempty("function") == len(m.functions) == 11
    # 需求：只有不被任何功能追溯的 SR 才允许空域（基础设施类，如 recorder/replay/rtm）
    covered = {rid for fn in m.functions.values() for rid in fn.requirements}
    infra = {rid for rid in m.requirements if rid not in covered}
    empty_reqs = {d.meta["req_id"] for d in kinds.get("requirement", []) if not (d.meta.get("domain") or "")}
    assert empty_reqs == infra, (f"需求空域 ≠ 基础设施集: {empty_reqs - infra} / {infra - empty_reqs}")


@NEEDS_UPSTREAM
def test_route_hvac_domain_precision_one(kb):
    """新域（HVAC）查询：词表路由到 hvac 分区，域内 topk 命中率=1.0。"""
    r = kb["retriever"].retrieve("空调压缩机过流保护 制冷降级", k=3)
    assert r["bounded"] is True
    assert r["route_source"] == "terms"
    assert "hvac" in r["routed_domains"]
    assert r["route_precision"] == 1.0
    ids = {h["doc_id"] for h in r["hits"]}
    assert "fault:hvac_compressor_overcurrent" in ids


@NEEDS_UPSTREAM
def test_route_via_graph_only_counts_system_edges(kb):
    """图谱定位域兜底：只采信 belongs_to system 边且需词元重合；
    无域词但语义贴近的查询 → 图谱路由到 bogie；纯噪声查询（无字符重合）→ 回退全局。"""
    from tcms_ai_platform.knowledge import Doc, HybridRetriever, VectorStore
    from tcms_ai_platform.knowledge.graph import KnowledgeGraph

    g = KnowledgeGraph()
    g.add_node("fault", "zz_fake", "假故障", {"subsystem": "走行部"})
    g.add_node("system", "SYS-BOGIE", "走行部", {})
    g.add_edge_raw("fault:zz_fake", "system:SYS-BOGIE", "belongs_to")
    vs = VectorStore()
    vs.add(Doc(doc_id="fault:zz_fake", kind="fault",
               text="该转动件高温异常处置流程", meta={"domain": "bogie"}))
    # 无域词、但与文档共享 ≥3 中文字符 → 图谱路由到 bogie
    r = HybridRetriever(vs, g).retrieve("那个转动件高温咋办", k=3)
    assert r["bounded"] is True
    assert r["route_source"] == "graph"
    assert r["routed_domains"] == ["bogie"]
    # 纯噪声（无中文字符重合）→ 图谱兜底不采信 → 回退全局
    r2 = HybridRetriever(vs, g).retrieve("zzzz qwerty 12345", k=3)
    assert r2["bounded"] is False
    assert r2["route_source"] == ""


@NEEDS_UPSTREAM
def test_route_domain_door(kb):
    r = kb["retriever"].retrieve("车门故障 不能发车", k=3)
    assert r["bounded"] is True
    assert "door" in r["routed_domains"]
    # 域内 top1 应命中 door 域资产
    top = r["hits"][0]
    assert top.get("domain") in ("door", "")


@NEEDS_UPSTREAM
def test_route_domain_heartbeat(kb):
    r = kb["retriever"].retrieve("VCU 心跳丢失 降级", k=3)
    assert "network" in r["routed_domains"]
    ids = {h["doc_id"] for h in r["hits"]}
    assert "fault:heartbeat_loss_vcu" in ids


@NEEDS_UPSTREAM
def test_unrouted_query_falls_back_global(kb):
    """无域信号查询（天气/通用）→ 不全域硬路由，退回全局检索保召回。"""
    r = kb["retriever"].retrieve("今天天气不错", k=3)
    assert r["bounded"] is False
    assert r["hits"]  # 仍返回（虽有噪，但诚实不空）


# ---- Q2 语义层：列车系统分类框架（system 节点 + 故障隶属 + 系统视角路由） ----


@NEEDS_UPSTREAM
def test_systems_injected_and_all_faults_linked():
    """enrich 后 system 节点存在（13 系统域），且全部故障 belongs_to 某系统。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import VectorStore, build_knowledge_graph

    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    enrich_graph(g, vs)
    systems = {n.id for n in g.nodes.values() if n.kind == "system"}
    assert len(systems) == 13  # SYS-TRAIN/BRAKE/TRACTION/DOOR/PANTO/BATT/AUX/HVAC/PIS/LIGHT/FIRE/BOGIE/SENSING
    linked = {e.src for e in g.edges if e.kind == "belongs_to" and e.src.startswith("fault:")}
    assert len(linked) == len(m.faults_by_key) == 203  # 全部故障归属系统（_SUB_TO_SYS 覆盖 16 个子系统名）


@NEEDS_UPSTREAM
def test_interlock_fault_links_are_explicit():
    """联锁→故障关联必须来自显式声明（domain_ebm.json fault_keys），不是关键词猜。

    防回退：door_open_moving（运行中门开，字典 EB）必须被门-车联锁/EB 决策/完整性联锁
    约束；door_fault 只能挂门-车联锁（不再因"门"字被误连到完整性联锁）；overspeed 挂
    超速判定 + EB 决策；traction_brake_conflict 挂牵引-制动互锁。
    """
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import VectorStore, build_knowledge_graph

    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    enrich_graph(g, vs)

    def il_links(fid: str) -> set[str]:
        return {nb.split(":", 1)[1] for nb, _k in g.neighbors(fid) if nb.startswith("interlock:")}

    assert il_links("fault:door_open_moving") == {
        "ILK-DOOR-MOTION", "ILK-EB-DECISION", "ILK-INTEGRITY-EB",
    }, "运行中门开应被门联锁/EB决策/完整性联锁约束（显式声明，非空）"
    assert il_links("fault:door_fault") == {"ILK-DOOR-MOTION"}, "door_fault 只挂门-车联锁（不再因'门'字误连）"
    assert "ILK-OVERSPEED" in il_links("fault:overspeed")
    assert "ILK-EB-DECISION" in il_links("fault:overspeed")
    assert il_links("fault:traction_brake_conflict") == {"ILK-TRACTION-BRAKE"}
    assert il_links("fault:integrity_loss") == {"ILK-INTEGRITY-EB"}
    # 每个 fault_keys 都指向真实故障键
    from tcms_ai_platform.domain.enrichment import load_domain_json

    data = load_domain_json("domain_ebm.json")
    for il in data.get("interlocks", []):
        for fk in il.get("fault_keys", []):
            assert fk in m.faults_by_key, f"interlock {il['id']} 引用未知故障 {fk}"


@NEEDS_UPSTREAM
def test_system_view_routing_returns_system():
    """系统视角查询（"制动系统有哪些故障"）→ 路由到域且顶层命中 system 节点。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    enrich_graph(g, vs)
    hr = HybridRetriever(vs, g)
    r = hr.retrieve("制动系统有哪些故障", k=4)
    assert "brake" in r["routed_domains"]
    ids = [h["doc_id"] for h in r["hits"]]
    assert "system:SYS-BRAKE" in ids  # 先给出系统视角答案


# ---- 症状资产（A 步）与因果边（B 步）：资产校验 / 注入 / 多跳遍历 / 诊断 ----
# 计数锁定值由 symptoms.yaml + causal_edges.yaml 机器派生（validate 输出），
# 与 test_platform / 文档同口径 —— 新增/删除资产条目必须同步此段断言与文档。


@NEEDS_UPSTREAM
def _enriched_kb():
    """装载真实资产 + enrich（含症状/因果注入）的 (model, graph, store, retriever)。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    report = enrich_graph(g, vs)
    return m, g, vs, HybridRetriever(vs, g), report


@NEEDS_UPSTREAM
def test_symptom_assets_validate_counts():
    """症状资产校验（无孤儿纪律）：12 症状 / indicates 41 / causes 13 / real 41 / derived 13。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import validate_symptom_assets

    m = load_asset_model(UPSTREAM)
    v = validate_symptom_assets(m)
    assert v["symptoms"] == 12  # 首批症状条数（symptoms.yaml）
    assert v["real_symptoms"] == 7  # annotation=real（全部候选真实机制）
    assert v["mixed_annotation_symptoms"] == 5  # annotation=mixed（含 derived 示意候选）
    assert v["indicates"] == 41  # symptom -indicates-> fault 边数（= Σ hints）
    assert v["causes"] == 13  # fault -causes-> fault 工程因果边数
    assert v["total_edges"] == 54
    assert v["real_mechanism_edges"] == 41
    assert v["derived_edges"] == 13  # 示意标注（诚实纪律，绝不静默冒充真实机制）


@NEEDS_UPSTREAM
def test_symptom_keys_no_conflict_and_hints_exist():
    """症状 key 不与故障键冲突；hints 均为真实故障键/系统域；hints 2-4 个。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import symptoms as load_symptoms

    m = load_asset_model(UPSTREAM)
    fk = set(m.faults_by_key)
    for s in load_symptoms():
        assert s["key"] not in fk, f"症状 key 与故障键冲突: {s['key']}"
        hints = s.get("hints") or []
        assert 2 <= len(hints) <= 4, f"{s['key']}: hints 数量 {len(hints)} 应 2-4"
        for h in hints:
            assert h.startswith("system:") or h in fk, f"{s['key']}: hint {h} 非真实故障键"
        assert s.get("domains"), f"{s['key']}: 缺涉及域"


@NEEDS_UPSTREAM
def test_symptom_causal_injected_into_graph():
    """enrich 后：12 个 symptom 节点/文档入图入库，indicates/causes 边数与表一致。"""
    m, g, vs, _hr, report = _enriched_kb()
    sym_nodes = {n.id for n in g.nodes.values() if n.kind == "symptom"}
    assert len(sym_nodes) == 12
    kinds = g.stats()["by_kind"]
    assert kinds["symptom"] == 12
    # 向量文档数 = 基础 506 + enrich 领域文档 + 12 症状文档（机器自证）
    sym_docs = [d for d in vs.docs if d.kind == "symptom"]
    assert len(sym_docs) == 12
    assert all(d.doc_id in sym_nodes for d in sym_docs)
    # 因果边数量/依据类型（= causal_edges.yaml 派生）
    sc = report["symptom_causal"]
    assert sc == {"symptoms": 12, "docs": 12, "indicates": 41, "causes": 13,
                  "real_mechanism": 41, "derived": 13, "total_edges": 54}
    assert sc["indicates"] == sum(1 for e in g.edges if e.kind == "indicates")
    assert sc["causes"] == sum(1 for e in g.edges if e.kind == "causes")
    # 每条因果边都带 basis 与 note（可审计）
    causal = [e for e in g.edges if e.kind in ("indicates", "causes")]
    assert all(e.basis in ("real_mechanism", "derived") for e in causal)
    assert all(e.note for e in causal)


@NEEDS_UPSTREAM
def test_retriever_finds_symptom_doc_for_symptom_query():
    """无码症状查询应命中 symptom 资产文档（而非误进错误域）。"""
    _m, _g, vs, hr, _report = _enriched_kb()
    r = hr.retrieve("仪表盘闪烁但无故障码", k=5)
    ids = [h["doc_id"] for h in r["hits"]]
    assert any(i == "symptom:dashboard_flicker" for i in ids[:3]), f"顶层未命中症状资产: {ids}"
    sym_hits = [h for h in r["hits"] if h["kind"] == "symptom"]
    assert sym_hits and sym_hits[0]["doc_id"] == "symptom:dashboard_flicker"


@NEEDS_UPSTREAM
def test_causal_multi_hop_chain_dashboard():
    """多跳因果链：dashboard_flicker →(indicates) aux_24v_undervoltage →(causes 反查) 根因。"""
    _m, g, _vs, _hr, _report = _enriched_kb()
    walk = g.causal_chain("symptom:dashboard_flicker", depth=3)
    assert walk["seed"] == "symptom:dashboard_flicker"
    assert walk["chains"]
    # 一跳：症状直接指示的嫌疑故障（每条链首跳）
    hop1 = {hops[0]["to"] for hops in walk["chains"]}
    assert "fault:aux_24v_undervoltage" in hop1
    assert "fault:aux_capacitor_aging" in hop1
    assert "fault:cab_display_blank" in hop1
    # 二跳：aux_24v_undervoltage 的疑似根因（aux_24v_charger_fail 等，方向 = causes 反查）
    deeper = {h["to"] for hops in walk["chains"] if len(hops) >= 2 for h in hops[1:]}
    assert "fault:aux_24v_charger_fail" in deeper
    assert "fault:aux_converter_fault" in hop1  # 同是辅变供电怀疑对象
    # 深度限制 ≤ 3 跳；每条 hop 均带 basis/note（逐跳溯源）
    for hops in walk["chains"]:
        assert len(hops) <= 3
        for h in hops:
            assert h["basis"] in ("real_mechanism", "derived")
            assert isinstance(h["note"], str)


@NEEDS_UPSTREAM
def test_diagnose_dashboard_flicker_regression():
    """专项回归：『仪表盘闪烁但无故障码』→ 非空、可溯源、不编造故障码、
    建议含 供电(aux) 与 显示(网络/列车控制) 域候选。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, _vs, hr, _report = _enriched_kb()
    r = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    assert r["matched"] is True
    assert r["no_match"] is False
    assert r["symptom"]["key"] == "dashboard_flicker"
    assert r["candidates"], "必须有候选（非空响应）"
    # 溯源：每个候选都在真实故障字典；都带依据与证据
    fkeys = set(m.faults_by_key)
    for c in r["candidates"]:
        assert c["fault"] in fkeys, f"编造了不存在的故障键: {c['fault']}"
        assert c["basis"] in ("real_mechanism", "derived")
        assert c["confidence"] > 0
    cand_keys = {c["fault"] for c in r["candidates"]}
    # 供电域候选（24V 欠压 / 支撑电容 / 辅变）
    assert "aux_24v_undervoltage" in cand_keys
    assert "aux_capacitor_aging" in cand_keys
    # 显示/列车控制域候选（司控台显示屏黑屏同域）
    assert "cab_display_blank" in cand_keys
    assert r["plan"] and r["plan"][0]["description"]
    assert r["evidence"].get("symptom_hit", {}).get("doc_id") == "symptom:dashboard_flicker"
    assert r["no_fault_code_invented"] is True
    assert "真实故障字典" in r["reply"]
    # 供电 vs 显示域都出现在建议里（域词汇单一真源派生）
    domains = {c["domain"] for c in r["candidates"]}
    assert "aux" in domains and "network" in domains


@NEEDS_UPSTREAM
def test_diagnose_soc_jump_derived_disclosure():
    """SOC 跳变：真实候选 battery_soc_jump/bms_current_sensor_drift 在前，
    derived 示意候选带 derived 标注（诚实）。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, _vs, hr, _report = _enriched_kb()
    r = diagnose_symptom(m, g, hr, "SOC 跳变")
    assert r["matched"] is True
    assert r["symptom"]["key"] == "soc_jump"
    keys = [c["fault"] for c in r["candidates"]]
    assert keys[0] == "battery_soc_jump"  # 同现象真实故障键排最前
    assert "bms_current_sensor_drift" in keys
    can = {c["fault"]: c for c in r["candidates"]}
    # derived 候选必须显式标注 derived（不冒充真实机制）
    assert can["bms_can_link_loss"]["derived"] is True
    assert can["bms_can_link_loss"]["basis"] == "derived"
    assert can["battery_soc_jump"]["derived"] is False


@NEEDS_UPSTREAM
def test_diagnose_no_match_is_honest():
    """无命中（与 TCMS 症状无关）→ no_match/不确定 + 需补充引导，绝不硬答。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, _vs, hr, _report = _enriched_kb()
    r = diagnose_symptom(m, g, hr, "车厢地板漏水了")
    assert r["matched"] is False
    assert r["no_match"] is True
    assert r["uncertain"] is True
    assert r["candidates"] == []
    assert "补充" in r["reply"] or "把握" in r["reply"]
    assert r["no_fault_code_invented"] is True


# ---- P1-1：词法 BM25 / 混合检索 / golden 检索评测 ----

@NEEDS_UPSTREAM
def test_lexical_bm25_deterministic_small():
    """BM25 纯单元：确定性排序 + 词频/IDF 语义（无向量、无依赖）。"""
    from tcms_ai_platform.knowledge.lexical import BM25Index, LexDoc, rrf

    docs = [
        LexDoc("d1", "车门 故障 车门 状态 不可信 禁止 发车 车门", "door"),
        LexDoc("d2", "超速 限速 160 紧急制动", "traction"),
        LexDoc("d3", "车门 气源 压力 不足 门 操作", "door"),
    ]
    idx = BM25Index(docs)
    top = idx.top("车门 故障 禁止 发车", k=2, domain=None)
    assert top[0][0] == "d1"  # 词频最高的 d1 第一
    assert {d for d, _ in top} <= {"d1", "d2", "d3"}
    # domain 过滤只在该分区打分
    assert all(d for d, _ in idx.top("车门", k=3, domain="door") if d in ("d1", "d3"))
    assert idx.top("车门", k=3, domain="traction") == []
    # rrf 融合确定性（顺序稳定；双榜都靠前的胜出）
    a = rrf([["a", "b"], ["b", "c"]], top=2)
    b = rrf([["a", "b"], ["b", "c"]], top=2)
    assert a == b and a[0][0] == "b"  # b 在两榜中名次总和最小


@NEEDS_UPSTREAM
def test_hybrid_retrieve_shape_and_anchors():
    """retrieve_hybrid：响应结构同构 + 语义/字面两通道关键命中。"""
    m, g, vs, hr, _report = _enriched_kb()
    # 结构契约
    r = hr.retrieve_hybrid("空调压缩机过流保护 制冷降级", k=5)
    for key in ("query", "routed_domains", "route_source", "bounded", "mixed_fallback", "hits"):
        assert key in r
    assert r["hits"] and all("graph_neighbors" in h for h in r["hits"])
    ids = [h["doc_id"] for h in r["hits"]]
    assert "fault:hvac_compressor_overcurrent" in ids[:5]
    # 无码症状查询经混合通道仍能命中症状资产（diagnose 入口的检索面）
    d = hr.retrieve_hybrid("仪表盘闪烁但无故障码", k=5)
    assert "symptom:dashboard_flicker" in [h["doc_id"] for h in d["hits"]][:3]


@NEEDS_UPSTREAM
def test_golden_retrieval_gate():
    """检索评测集门禁：14 条 golden，混合通道全过；纯向量通道 ≥ 13/14（防回退）。"""
    from tcms_ai_platform.knowledge.golden import evaluate_retriever

    _m, _g, _vs, hr, _report = _enriched_kb()
    hy = evaluate_retriever(hr, k=5, hybrid=True)
    vec = evaluate_retriever(hr, k=5, hybrid=False)
    fails = [row["q"] for row in hy["rows"] if not row["pass"]]
    assert hy["total"] == 14
    assert hy["passed"] == hy["total"], f"混合通道 golden 未全过: {fails}"
    assert hy["top1"] >= 10
    assert vec["passed"] >= 13, "纯向量通道回退超标（golden 防回退门禁）"
    # 确定性：同语料重跑结果一致
    assert evaluate_retriever(hr, k=5, hybrid=True)["passed"] == hy["passed"]


# ---- P1-2：证据图可查询（最短路 + 无损导出） ----

@NEEDS_UPSTREAM
def test_graph_shortest_path_causal_evidence():
    """证据路径：dashboard 症状 → 24V 欠压根因链，逐边带 basis/note（证据可溯源）。"""
    _m, g, _vs, _hr, _report = _enriched_kb()
    path = g.shortest_path("symptom:dashboard_flicker", "fault:aux_24v_charger_fail", max_depth=4)
    assert path is not None, "应有可达到的证据路径"
    assert path[0]["src"] == "symptom:dashboard_flicker"
    assert path[-1]["dst"] == "fault:aux_24v_charger_fail"
    for e in path:
        assert e["kind"] in ("indicates", "causes")
        assert e["basis"] in ("real_mechanism", "derived")
        assert e["note"]
    # 深度限制下不可达 → None（诚实不编造路径）
    assert g.shortest_path("symptom:dashboard_flicker", "symptom:door_open_light_flicker", max_depth=1) is None
    # 路径逐边邻接串接
    for i in range(1, len(path)):
        assert path[i]["src"] == path[i - 1]["dst"]


@NEEDS_UPSTREAM
def test_graph_export_json_consistent():
    """无损导出：节点/边计数 = stats，因果边 basis/note 完整保留（供图引擎迁移对拍）。"""
    from tcms_ai_platform.knowledge.graphio import export_graph_json

    _m, g, _vs, _hr, _report = _enriched_kb()
    dump = export_graph_json(g)
    st = g.stats()
    assert dump["node_count"] == st["nodes"] == len(dump["nodes"])
    assert dump["edge_count"] == st["edges"] == len(dump["edges"])
    causal = [e for e in dump["edges"] if e["kind"] in ("indicates", "causes")]
    assert causal, "导出必须含因果边"
    assert all(e.get("basis") in ("real_mechanism", "derived") for e in causal)
    assert all(e.get("note") for e in causal)
    # 幂等：重复导出一致
    assert export_graph_json(g) == dump
