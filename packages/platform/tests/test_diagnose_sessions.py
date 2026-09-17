"""P1-1 / P1-2 / P2-2 门禁：多轮锚点记忆、可分性澄清、置信度口径锁值。

- P1-1：session_anchor 证据引用式多轮 —— 追问复用上轮症状锚点、换题不误用、
  无锚点时追问诚实 no_match；AnchorMemory TTL/事实合并有测试。
- P1-2：候选 top1/top2 置信接近且同域/同跳 → clarification（追问区分性观测，
  不硬排第一、不发明）；可分性足够时不打扰。
- P2-2：_CONF 锁值 + 单调性（排序分口径，非概率；防未来漂移）。
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


# ---------------------------------------------------------------------------
# P1-1 多轮锚点记忆
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_multi_turn_follow_up_reuses_previous_symptom_anchor():
    """追问（"再说一下/那个部位"）直配落空 → 复用上轮症状锚点继续，诚实标注。"""
    from tcms_ai_platform.agent.diagnose_memory import build_anchor
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()
    r1 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    assert r1["symptom"]["key"] == "dashboard_flicker"
    anchor = build_anchor(r1, "仪表盘闪烁但无故障码")

    r2 = diagnose_symptom(
        m, g, hr, "再说一下刚才那个部位应该查哪个信号", depth=3, session_anchor=anchor
    )
    assert r2["session_anchor_used"] is True, "追问应标注使用了上一轮锚点"
    assert r2["no_match"] is False
    assert r2["symptom"]["key"] == "dashboard_flicker", "应沿用上一轮症状继续走链"
    assert r2["candidates"], "锚点复用后仍应产出候选"
    assert all(c["fault"] in m.faults_by_key for c in r2["candidates"]), "候选必须真实"
    assert "锚点" in r2["reply"], "reply 应如实告知沿锚点继续"
    sess = (r2["evidence"] or {}).get("session") or {}
    assert sess.get("anchor_used") is True
    assert sess.get("prior_symptom_key") == "dashboard_flicker"


@NEEDS_UPSTREAM
def test_multi_turn_topic_change_does_not_reuse_anchor():
    """换题（无指代词）→ 正常直配新症状，不误用上轮锚点。"""
    from tcms_ai_platform.agent.diagnose_memory import build_anchor
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()
    r1 = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    anchor = build_anchor(r1, "仪表盘闪烁但无故障码")

    r2 = diagnose_symptom(m, g, hr, "SOC 跳变", depth=3, session_anchor=anchor)
    assert r2["session_anchor_used"] is False
    assert r2["symptom"]["key"] == "soc_jump"


@NEEDS_UPSTREAM
def test_follow_up_without_anchor_stays_honest():
    """带指代词但无历史锚点 → 不得凭空作答，诚实 no_match。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()
    r = diagnose_symptom(m, g, hr, "再说一下刚才那个部位", depth=3)
    assert r["no_match"] is True
    assert r["candidates"] == []


def test_anchor_memory_ttl_cleanup_and_facts_cap():
    """TTL 过期清理 + 事实队列去重封顶（会话不无限增长）。"""
    from tcms_ai_platform.agent.diagnose_memory import AnchorMemory

    clock = {"t": 1_000.0}

    def now() -> float:
        return clock["t"]

    mem = AnchorMemory(ttl_s=600.0, now=now)
    mem.put("s1", {"symptom": {"key": "dashboard_flicker"}, "candidates": [], "facts": ["问一"]})
    mem.put("s1", {"symptom": {"key": "dashboard_flicker"}, "candidates": [], "facts": ["问二"]})
    mem.put("s1", {"symptom": {"key": "dashboard_flicker"}, "candidates": [], "facts": ["问一"]})
    a = mem.get("s1")
    assert a is not None
    assert a["facts"] == ["问二", "问一"], "事实队列去重且保留最近"

    clock["t"] += 601.0
    assert mem.get("s1") is None, "超过 TTL 应过期"
    # 再放一个过期项验证 cleanup 计数路径（get 已弹出 s1，故重建一个）
    mem.put("s2", {"symptom": {"key": "x"}, "candidates": [], "facts": ["旧"]})
    clock["t"] += 601.0
    assert mem.cleanup() >= 1


def test_anchor_memory_thread_safe_len():
    from tcms_ai_platform.agent.diagnose_memory import AnchorMemory

    mem = AnchorMemory()
    for i in range(5):
        mem.put(f"s{i}", {"symptom": {"key": "x"}, "candidates": [], "facts": [str(i)]})
    assert len(mem) == 5
    assert mem.get("s0")["symptom"]["key"] == "x"


# ---------------------------------------------------------------------------
# P1-2 可分性澄清
# ---------------------------------------------------------------------------


def test_ambiguity_pure_function_gates():
    """纯函数门禁：置信差 <0.15 且同域/同跳才追问；否则 None。"""
    from tcms_ai_platform.agent.diagnoser import _ambiguity

    base = {
        "fault": "a",
        "name": "A",
        "domain": "aux",
        "hop": 1,
        "confidence": 0.85,
        "check": "查 24V 母线电压",
    }
    # 同域同置信 → 澄清
    c = _ambiguity([dict(base), dict(base, fault="b", name="B", check="查 24V 电流")])
    assert c is not None and c["needs_more"] is True
    assert [x["fault"] for x in c["between"]] == ["a", "b"]
    assert "查 24V 母线电压" in c["distinguishing_observations"]
    # 差距 ≥0.15 → 不打扰
    assert _ambiguity([dict(base), dict(base, fault="b", confidence=0.7)]) is None
    # 不同域且不同跳 → 不硬判为可区分问题（不追问）
    assert (
        _ambiguity(
            [dict(base, fault="a", domain="aux", hop=1), dict(base, fault="b", domain="door", hop=2)]
        )
        is None
    )
    # 单候选 → None
    assert _ambiguity([dict(base)]) is None


@NEEDS_UPSTREAM
def test_diagnose_dashboard_ambiguity_asks_not_hard_ranks():
    """仪表盘闪烁：top 多名同域同置信 → clarification 追问区分性观测，不硬排。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()
    r = diagnose_symptom(m, g, hr, "仪表盘闪烁但无故障码", depth=3)
    clar = r.get("clarification")
    assert clar is not None and clar["needs_more"] is True
    top = clar["between"][0]["fault"]
    assert top == "aux_24v_undervoltage", "top 候选不变（只是不硬排，不发明）"
    assert clar["distinguishing_observations"], "区分性观测必须来自真实 check/场景"
    assert all(c["fault"] in m.faults_by_key for c in r["candidates"])
    assert "不硬排" in r["reply"]


@NEEDS_UPSTREAM
def test_diagnose_clear_leader_no_clarification():
    """制动灯常亮：top 领先足（real .85 vs derived .5）→ 不追问，正常出候选。"""
    from tcms_ai_platform.agent.diagnoser import diagnose_symptom

    m, g, hr = _kb()
    r = diagnose_symptom(m, g, hr, "制动灯常亮", depth=3)
    assert r["clarification"] is None
    assert r["candidates"] and r["candidates"][0]["fault"] == "tail_light_fail"


# ---------------------------------------------------------------------------
# P2-2 置信度口径锁值（排序分，非概率）
# ---------------------------------------------------------------------------


def test_confidence_table_locked_as_ranking_score():
    """_CONF 锁值：语义=依据充分性排序分（非概率）；漂移即失败。"""
    from tcms_ai_platform.agent.diagnoser import _confidence

    expected = {
        (1, "real_mechanism"): 0.85,
        (1, "derived"): 0.5,
        (2, "real_mechanism"): 0.7,
        (2, "derived"): 0.4,
        (3, "real_mechanism"): 0.6,
        (3, "derived"): 0.35,
    }
    for key, val in expected.items():
        assert _confidence(*key) == val, f"置信度表被改动: {key}"
    # 单调性：真实机制 > 同跳示意；跳数越深排序分越低（排序语义，非概率累积）
    for hop in (1, 2, 3):
        assert _confidence(hop, "real_mechanism") > _confidence(hop, "derived")
    assert _confidence(1, "real_mechanism") > _confidence(2, "real_mechanism") > _confidence(3, "real_mechanism")
    # 跳数越界 → clamp 到 3 跳（排序语义：不因越界给默认而破坏单调）
    assert _confidence(9, "real_mechanism") == _confidence(3, "real_mechanism")
    # 未知依据类型 → 回落默认
    assert _confidence(1, "unknown_basis") == 0.3
