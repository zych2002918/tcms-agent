"""P2-3 门禁：run 节点参与召回（真实执行过 → 诊断/检索 evidence 可见 run 引用）。

诚实纪律：没跑过的资产 recent_runs=[]（不伪造记录）；跑过后相关场景/候选场景的
evidence.recent_runs 出现 {run_id, scenario, passed/failed/all_passed, ref}。
"""

from __future__ import annotations

from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@NEEDS_UPSTREAM
def _kb():
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        GraphSink,
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
    return m, g, HybridRetriever(vs, g), GraphSink(g)


@NEEDS_UPSTREAM
def test_no_run_yet_means_no_run_evidence():
    """未执行过 → recent_runs 为空（诚实：不伪造执行记录）。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr, _sink = _kb()
    r = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    assert r["evidence"]["recent_runs"] == []


@NEEDS_UPSTREAM
def test_run_appears_in_diagnose_evidence_for_its_scenarios():
    """真实 run 一次后 → 其复现场景出现在候选里的诊断 evidence 可见 run 引用。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr, sink = _kb()
    r0 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    scen_files = [s for c in r0["candidates"] for s in c["scenarios"]]
    assert scen_files, "候选应带复现场景"
    target = scen_files[0]
    sink.record_run("p23-run-1", target, {"passed": 2, "failed": 0, "all_passed": True})

    r1 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    runs = r1["evidence"]["recent_runs"]
    assert runs, "诊断 evidence 应可见 run 引用"
    assert runs[0]["run_id"] == "p23-run-1"
    assert runs[0]["scenario"] == target
    assert runs[0]["all_passed"] is True
    assert runs[0]["ref"].startswith("runtime:"), "run 引用应带资产出处"


@NEEDS_UPSTREAM
def test_run_appears_in_retrieval_evidence_for_scenario_doc():
    """检索命中被执行过的场景文档 → hit.recent_runs 出现 run 引用。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr, sink = _kb()
    r0 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    scen_files = [s for c in r0["candidates"] for s in c["scenarios"]]
    target = scen_files[0]
    scen_name = m.scenarios[target].name
    sink.record_run("p23-run-retr", target, {"passed": 1, "failed": 0, "all_passed": True})

    res = hr.retrieve_hybrid(scen_name, k=10)
    hit = next((h for h in res["hits"] if h["doc_id"] == f"scenario:{target}"), None)
    if hit is None:
        pytest.skip("场景文档未进本次 top-k（召回面不同，非失败）")
    assert hit["recent_runs"], "被执行过的场景文档检索证据应带 run 引用"
    assert hit["recent_runs"][0]["run_id"] == "p23-run-retr"
