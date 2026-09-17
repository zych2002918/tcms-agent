"""t2 扩展测试：编排顾问对话（/api/agent/advisor + agent.advisor 决策核心）。

核心红线：输入无法匹配内存故障时**绝不 422**——进入多轮对话。
    - 「车门故障不能发车」→ intent=match_fault，fault=door_fault，附现成场景
    - 「今天天气不错」    → 不 422：out_of_domain（友好引导回 TCMS 主题）
    - 「门的问题」        → clarify 带真实故障候选（door_fault 在前）+ RAG 证据
    - 编排意图            → compose_scenario，suggested_steps 可直接 /api/run/custom
    - 空消息/任意输入      → 200（永不 422）
    - 确定性：无 key 时用规则模板（llm_generated=false）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.agent.advisor import advisor_turn
from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.domain import enrich_graph
from tcms_ai_platform.knowledge import (
    HybridRetriever,
    VectorStore,
    build_docs_from_asset,
    build_knowledge_graph,
)

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def model():
    return load_asset_model(UPSTREAM)


@pytest.fixture(scope="module")
def retriever(model):
    g = build_knowledge_graph(model)
    store = VectorStore()
    store.add_many(build_docs_from_asset(model))
    enrich_graph(g, store)
    return HybridRetriever(store, g)


@NEEDS_UPSTREAM
def test_advisor_door_match(model, retriever):
    """「车门故障不能发车」→ match_fault / door_fault / derate + 现成场景建议。"""
    t = advisor_turn(model, retriever, "车门故障不能发车", use_llm=False)
    assert t.intent == "match_fault"
    assert t.matched_fault == "door_fault"
    assert t.fault_matches and t.fault_matches[0]["key"] == "door_fault"
    assert t.fault_matches[0]["action"] == "derate"
    assert t.fault_matches[0]["confidence"] >= 0.5
    assert not t.needs_clarification
    # 现成覆盖场景可点击（贴近真实测试工作流）
    assert any(s["file"] == "door_cascade.yaml" for s in t.scenario_suggestions)
    assert "车门" in t.reply and "door_fault" in t.reply


@NEEDS_UPSTREAM
def test_advisor_weather_no_422_out_of_domain(model, retriever):
    """「今天天气不错」→ 绝不 422：out_of_domain + 友好引导回 TCMS 主题。"""
    t = advisor_turn(model, retriever, "今天天气不错", use_llm=False)
    assert t.intent == "out_of_domain"
    assert t.fault_matches == []
    assert t.needs_clarification
    # 规则模板诚实引导（不假装理解）
    assert "TCMS" in t.reply or "故障" in t.reply
    # 永不抛 NoFaultMatch（对比 /api/agent/free 的 422 红线）
    from tcms_ai_platform.agent.freeform import NoFaultMatch

    with pytest.raises(NoFaultMatch):
        from tcms_ai_platform.agent.freeform import parse_free_goal

        parse_free_goal(model, "今天天气不错", use_llm=False)


@NEEDS_UPSTREAM
def test_advisor_door_ambiguous_clarify(model, retriever):
    """「门的问题」（规则零候选）→ clarify：RAG 语义候选 door 系在前 + 证据链。"""
    t = advisor_turn(model, retriever, "门的问题", use_llm=False)
    assert t.intent == "clarify"
    assert t.needs_clarification
    keys = [f["key"] for f in t.fault_matches]
    # 门语义的故障应排在候选前（语义排序修正，非噪声中枢顶位）
    assert "door_fault" in keys[:3]
    assert any(f["confidence"] >= 0.5 for f in t.fault_matches)
    assert t.followup_question
    assert "确认" in t.reply or "哪个" in t.reply


@NEEDS_UPSTREAM
def test_advisor_weather_even_with_llm_never_fabricates(model, retriever):
    """红线（HTTP 层/决策层一致）：规则零候选时即使 LLM 可用也不许自由发明真实键。"""
    # LLM 注入想硬猜 overspeed → 决策层只允许澄清/引导，不允许把键落进 fault_matches
    t = advisor_turn(
        model,
        retriever,
        "今天天气不错",
        use_llm=True,
        llm_chat=lambda s, u: '{"intent": "match_fault", "fault": "overspeed"}',
    )
    assert t.intent == "out_of_domain"
    assert t.fault_matches == []  # 零候选时 LLM 不能把 overspeed 塞进候选


@NEEDS_UPSTREAM
def test_advisor_empty_never_422(model, retriever):
    """空消息/纯空白 → 200：引导用户描述（永不 422）。"""
    for msg in ("", "   ", "。", "??"):
        t = advisor_turn(model, retriever, msg, use_llm=False)
        assert t.reply
        assert t.needs_clarification


@NEEDS_UPSTREAM
def test_advisor_compose_scenario_steps(model, retriever):
    """编排意图 → compose_scenario：suggested_steps 可执行（含真实 expect）。"""
    t = advisor_turn(
        model,
        retriever,
        "我想编排车门故障加超速的级联序列",
        use_llm=False,
    )
    assert t.intent == "compose_scenario"
    assert t.suggested_steps
    inj = [s for s in t.suggested_steps if s["action"] == "inject"]
    rec = [s for s in t.suggested_steps if s["action"] == "recover"]
    assert {s["fault"] for s in inj} == {"door_fault", "overspeed"}
    # 草稿直接可被 /api/run/custom 消费（真实故障键 + expect 处置）
    for s in inj:
        assert s["fault"] in {"door_fault", "overspeed"}
        assert s["expect"] in {"derate", "emergency_brake", "shutdown", "warning", "none"}
    assert rec and {s["fault"] for s in rec} == {"door_fault", "overspeed"}
    # 递增时间戳
    ats = [s["at"] for s in t.suggested_steps]
    assert ats == sorted(ats)


@NEEDS_UPSTREAM
def test_advisor_llm_polish_falls_back_without_key(model, retriever, monkeypatch, tmp_path):
    """无 key → 不做 LLM 调用，规则模板回复（离线确定性，诚实标注）。"""
    for k in ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path / "home"))  # 隔离设置层（GUI 存的 key 不入测）
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(Path("Z:/no-cred.yaml")))
    t = advisor_turn(model, retriever, "验证超速降级", use_llm=True)
    assert t.intent == "match_fault"
    assert t.matched_fault == "overspeed"
    assert t.llm_generated is False
    assert "超速" in t.reply
