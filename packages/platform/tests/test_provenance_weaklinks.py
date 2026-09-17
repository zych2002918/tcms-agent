"""P2-1 / P1-4 门禁：出处链(file:key) + 弱证据升级(资产锚点/2 跳路径)。

- P2-1：54 因果边 + 12 症状全有出处（端点可解析为资产 file:key 且 key 真实存在）；
  /api/agent/diagnose 的 evidence 逐链可点到资产文件级（edge_refs + candidate
  chains[].refs）。
- P1-4：检索命中携带 source_ref/anchor_stats/weak_links —— weak_links 路径节点
  全部真实存在于图（不发明），ref 格式为资产出处；邻接资产出处可机器追溯。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

_REF_RE = re.compile(r"^[A-Za-z0-9_./\-]+(:[A-Za-z0-9_.\-]+)?$")


@NEEDS_UPSTREAM
def _kb():
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
    return m, g, HybridRetriever(vs, g)


@NEEDS_UPSTREAM
def test_causal_edges_and_symptoms_have_traceable_source():
    """54 因果边 + 12 症状：端点可解析出处，key 全真实（不伪造）。"""
    from tcms_ai_platform.agent.diagnoser import _asset_refs
    from tcms_ai_platform.domain.causal import symptoms as _catalog_symptoms

    m, g, _hr = _kb()
    causal = [
        e for e in g.edges if e.kind in ("indicates", "causes")
    ]
    assert len(causal) == 54, "因果边数应与因果表一致(54)"
    sym_keys = {s["key"] for s in _catalog_symptoms()}
    fault_keys = set(m.faults_by_key)

    for e in causal:
        refs = _asset_refs([e.src, e.dst])
        assert refs, f"边无出处: {e.src} {e.kind} {e.dst}"
        for r in refs:
            assert _REF_RE.match(r), f"出处格式异常: {r!r}"
            file_, key = r.rsplit(":", 1)
            if file_ == "symptoms.yaml":
                assert key in sym_keys, f"症状出处 key 不存在: {key}"
            elif file_ == "faults.yaml":
                assert key in fault_keys, f"故障出处 key 不存在: {key}"


@NEEDS_UPSTREAM
def test_diagnose_evidence_chain_sources_are_asset_level():
    """诊断 evidence 逐链可点到资产：edge_refs + 每条候选 chain refs 真实。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()
    r = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    assert r["evidence"]["edge_refs"], "evidence 应带 edge_refs"
    assert all(_REF_RE.match(x) for x in r["evidence"]["edge_refs"])
    seen_fault_refs = 0
    for c in r["candidates"]:
        for chain in c.get("chains", []):
            assert chain.get("refs"), f"候选 {c['fault']} 的链缺 refs"
            for ref in chain["refs"]:
                assert _REF_RE.match(ref)
                if ref.startswith("faults.yaml:"):
                    key = ref.split(":", 1)[1]
                    assert key in m.faults_by_key
                    seen_fault_refs += 1
    assert seen_fault_refs >= 1


@NEEDS_UPSTREAM
def test_http_diagnose_evidence_has_edge_refs():
    """HTTP：/api/agent/diagnose 的 evidence 与候选链可点到资产。"""
    from fastapi.testclient import TestClient

    from tcms_ai_platform.server.app import create_app

    app = create_app(upstream=UPSTREAM)
    client = TestClient(app)
    body = client.post("/api/agent/diagnose", json={"message": "仪表盘闪烁但无故障码"}).json()
    assert body["evidence"]["edge_refs"]
    refs = body["evidence"]["edge_refs"]
    assert all(_REF_RE.match(x) for x in refs)
    some = [c for c in body["candidates"] if c.get("chains")]
    assert some, "候选应带链条"
    assert all(ch.get("refs") for c in some for ch in c["chains"][:1])


@NEEDS_UPSTREAM
def test_retrieve_hits_carry_asset_refs_and_anchor_stats():
    """P1-4：检索命中带 source_ref/anchor_stats；邻接出处可机器追溯。"""
    m, g, hr = _kb()
    res = hr.retrieve_hybrid("车门故障 关门联锁", k=5)
    assert res["hits"]
    for h in res["hits"]:
        assert h["source_ref"] and _REF_RE.match(h["source_ref"])
        assert isinstance(h["anchor_stats"], dict)
        for nb in h["graph_neighbors"]:
            assert nb["ref"] and _REF_RE.match(nb["ref"])
            assert nb["id"] in g.nodes, "邻接节点必须真实存在于图"


@NEEDS_UPSTREAM
def test_retrieve_weak_links_exist_only_real_nodes():
    """P1-4：weak_links（2 跳弱路径）节点全部真实、出处可溯、不发明。"""
    m, g, hr = _kb()
    res = hr.retrieve_hybrid("紧急制动 超速 保护", k=6)
    links = [link for h in res["hits"] for link in h.get("weak_links", [])]
    if not links:
        pytest.skip("当前语料未产出 2 跳弱路径样例（结构稀疏，非失败）")
    for link in links:
        assert len(link["path"]) == 3
        for node_id in link["path"]:
            assert node_id in g.nodes, f"弱路径节点不在图（发明）: {node_id}"
        assert _REF_RE.match(link["middle"]["ref"])
        assert _REF_RE.match(link["target"]["ref"])
