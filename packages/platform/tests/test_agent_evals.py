"""P1-3 Agent 评测复用：diagnose/free 解析 golden 回归 + harness 摘要指标。

评测集：src/tcms_ai_platform/domain/data/agent_golden.yaml（期望值全部真实资产）。
门禁：诊断 8/8、自由解析 5/5、候选“不编造”逐条校验；摘要指标纯函数单测。
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
    """真实资产 + enrich 的 (m, g, retriever)。"""
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
def test_agent_golden_diagnose():
    """诊断 golden：8/8 全过；候选全部真实故障键（不编造）。"""
    from tcms_ai_platform.agent.evals import evaluate_diagnose

    m, g, hr = _kb()
    ev = evaluate_diagnose(m, g, hr)
    fails = [r["q"] for r in ev["rows"] if not r["pass"]]
    assert ev["total"] == 8
    assert ev["passed"] == ev["total"], f"诊断 golden 未全过: {fails}"
    assert all(not r["fabricated"] for r in ev["rows"])  # 红线：不编造故障键


@NEEDS_UPSTREAM
def test_agent_golden_free_parse():
    """自由目标解析 golden：5/5（确定性规则，不开 LLM）；无匹配走 NoFaultMatch。"""
    from tcms_ai_platform.agent.evals import evaluate_free_parse

    m, _g, _hr = _kb()
    ev = evaluate_free_parse(m)
    fails = [r["q"] for r in ev["rows"] if not r["pass"]]
    assert ev["total"] == 5
    assert ev["passed"] == ev["total"], f"free 解析 golden 未全过: {fails}"


def test_agent_summary_metrics():
    """harness 结果摘要：达成率 / 自愈 / 证据使用 / radar 均值（与 TaskRun.score 口径一致）。"""
    from tcms_ai_platform.agent.evals import summarize_agent_results

    runs = [
        {"achieved": True, "reflected": False, "evidence": [{"doc_id": "a"}], "score": {"radar": {"goal_achieved": 100, "evidence_used": 25, "exec_pass": 100, "reflection": 0}}},
        {"achieved": True, "reflected": True, "evidence": [{"doc_id": "b"}], "score": {"radar": {"goal_achieved": 100, "evidence_used": 50, "exec_pass": 100, "reflection": 100}}},
        {"achieved": False, "reflected": True, "evidence": None, "score": {"radar": {"goal_achieved": 0, "evidence_used": 0, "exec_pass": 0, "reflection": 50}}},
    ]
    s = summarize_agent_results(runs)
    assert s["total"] == 3
    assert s["achieved"] == 2 and s["pass_rate"] == round(2 / 3, 3)
    assert s["self_healed"] == 1
    assert s["evidence_used_runs"] == 2
    assert s["radar_mean"]["goal_achieved"] == round(200 / 3, 1)
    assert s["radar_mean"]["reflection"] == 50.0
    # 空结果不炸
    assert summarize_agent_results([])["total"] == 0 and summarize_agent_results([])["pass_rate"] == 0.0


@NEEDS_UPSTREAM
def test_diagnose_llm_arbitration_only_reorders_candidates():
    """LLM 候选内仲裁：只重排真实候选；编造键被丢弃；llm_generated=true。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()

    # fake LLM：把 cab_display_blank 提到第一，并夹带一个不存在的键（应被过滤）
    def fake(_sys, _usr):
        return '["fault:xxx_not_real", "cab_display_blank", "aux_capacitor_aging"]'

    rule = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", use_llm=False)
    arb = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", use_llm=True, llm_chat=fake)

    rule_keys = [c["fault"] for c in rule["candidates"]]
    arb_keys = [c["fault"] for c in arb["candidates"]]
    assert set(arb_keys) == set(rule_keys), "仲裁不得增删候选（只重排）"
    assert arb_keys[0] == "cab_display_blank" and rule_keys[0] != "cab_display_blank"
    assert all(k in m.faults_by_key for k in arb_keys)  # 红线：不编造
    assert arb["llm_generated"] is True and rule["llm_generated"] is False


@NEEDS_UPSTREAM
def test_diagnose_llm_failure_falls_back_to_rules():
    """LLM 失败/非法输出 → 规则排序兜底（llm_generated=false、顺序不变）。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()

    def broken(_sys, _usr):
        raise RuntimeError("remote down")

    def bad_chat(_s, _u):
        return "我不太确定，可能不是这些……"
    r0 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", use_llm=False)
    r1 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", use_llm=True, llm_chat=broken)
    r2 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", use_llm=True, llm_chat=bad_chat)
    for r in (r1, r2):
        assert r["llm_generated"] is False
        assert [c["fault"] for c in r["candidates"]] == [c["fault"] for c in r0["candidates"]]
