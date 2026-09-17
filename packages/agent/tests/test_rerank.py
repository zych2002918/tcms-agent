"""重排：在融合结果上按可解释特征重新排序。

用**真实评测集**（platform 的 14 条检索 golden）当作重排的回归门禁——
"重排有没有用"不靠感觉，靠 min_at 达标率与 top-1 命中率的实测数字。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tcms_agent.rerank import FEATURES, apply_to_kb_result, compute_features, rerank_hits


def _find_golden() -> Path | None:
    """向上搜索 golden 文件，而不是数 parents[N]。

    数层级极易在目录调整后静默失效（本仓就踩过：`parents[3]` 少算一层 →
    三条测试全部 skip，套件依然"全绿"）。改成搜索后，路径变化会**失败**而不是跳过。
    """
    here = Path(__file__).resolve()
    for base in here.parents:
        cand = base / "packages" / "platform" / "src" / "tcms_ai_platform" / "domain" / "data" / "retrieval_golden.yaml"
        if cand.is_file():
            return cand
        cand2 = base / "platform" / "src" / "tcms_ai_platform" / "domain" / "data" / "retrieval_golden.yaml"
        if cand2.is_file():
            return cand2
    return None


GOLDEN = _find_golden()


def _hit(doc_id: str, *, text: str = "", kind: str = "fault", channels=None) -> dict:
    return {
        "doc_id": doc_id,
        "kind": kind,
        "text": text,
        "channels": channels if channels is not None else ["vector", "lexical"],
    }


# ---------------------------------------------------------------------------
# 特征
# ---------------------------------------------------------------------------


def test_channel_consensus_rewards_multi_channel_hits() -> None:
    q = "车门故障"
    one = compute_features(q, _hit("fault:a", channels=["vector"]))
    three = compute_features(q, _hit("fault:a", channels=["vector", "lexical", "graph"]))
    assert one.channel_consensus == pytest.approx(1 / 3)
    assert three.channel_consensus == 1.0


def test_query_overlap_uses_cjk_chars_and_latin_words() -> None:
    f = compute_features("车门故障", _hit("fault:a", text="车门出现故障"))
    assert f.query_overlap == 1.0
    f2 = compute_features("车门故障", _hit("fault:a", text="完全无关的内容"))
    assert f2.query_overlap == 0.0


def test_id_hit_is_a_strong_signal() -> None:
    assert compute_features("door_fault", _hit("fault:door_fault")).id_hit == 1.0
    assert compute_features("overspeed", _hit("fault:door_fault")).id_hit == 0.0


def test_kind_prior_prefers_test_engineering_assets() -> None:
    assert compute_features("x", _hit("fault:a", kind="fault")).kind_prior > 0
    assert compute_features("x", _hit("concept:a", kind="concept")).kind_prior == 0.0


def test_features_are_bounded_and_explainable() -> None:
    f = compute_features("车门故障", _hit("fault:door_fault", text="车门故障"))
    d = f.to_dict()
    assert set(d) == set(FEATURES), "特征名必须与权重表一致（否则权重形同虚设）"
    assert all(0.0 <= v <= 1.0 for v in d.values()), f"特征应归一化到 [0,1]: {d}"


# ---------------------------------------------------------------------------
# 只重排、不增删
# ---------------------------------------------------------------------------


def test_rerank_only_reorders_never_adds_or_drops() -> None:
    hits = [_hit(f"fault:{i}", text="车门故障") for i in range(5)]
    out = rerank_hits("车门故障", hits)
    assert len(out) == len(hits)
    assert {h["doc_id"] for h in out} == {h["doc_id"] for h in hits}
    assert all("rerank_score" in h and "features" in h for h in out)
    assert all("rrf_rank" in h for h in out), "要能对照重排前后名次"


def test_rerank_is_deterministic() -> None:
    hits = [_hit(f"fault:{i}", text="车门故障") for i in range(5)]
    assert [h["doc_id"] for h in rerank_hits("车门故障", hits)] == [
        h["doc_id"] for h in rerank_hits("车门故障", hits)
    ]


def test_rerank_promotes_multi_channel_hit() -> None:
    """三路都召回的条目应被提到只被一路召回的前面。"""
    hits = [
        _hit("fault:weak", channels=["vector"]),
        _hit("fault:strong", channels=["vector", "lexical", "graph"]),
    ]
    out = rerank_hits("车门故障", hits)
    assert out[0]["doc_id"] == "fault:strong"


def test_rerank_empty_is_safe() -> None:
    assert rerank_hits("q", []) == []


def test_apply_to_kb_result_annotates_and_keeps_shape() -> None:
    res = {"query": "车门故障", "hits": [_hit("fault:a")], "routed_domains": ["door"]}
    out = apply_to_kb_result("车门故障", res)
    assert out["reranked"] is True
    assert out["routed_domains"] == ["door"], "其余字段必须原样保留"
    assert out["hits"][0]["rerank_score"] is not None


def test_apply_to_kb_result_noop_when_no_hits() -> None:
    res = {"hits": []}
    assert apply_to_kb_result("q", res) == res
    assert apply_to_kb_result("q", {}) == {}


# ---------------------------------------------------------------------------
# 真实评测集：重排必须不劣化，且实测提升 top-1
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def goldens() -> list[dict]:
    # 找不到就**失败**而不是跳过：静默 skip 会让"重排评测"变成摆设。
    assert GOLDEN is not None and GOLDEN.is_file(), (
        "找不到检索 golden 文件（重排的评测依据）；请在 monorepo 根下运行测试"
    )
    return list(yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["queries"])


@pytest.fixture(scope="module")
def retriever():
    from tcms_agent.knowledge import build_knowledge

    return build_knowledge().retriever


def _first_rank(hits: list[dict], expect: set[str]) -> int:
    for i, h in enumerate(hits, start=1):
        if h["doc_id"] in expect:
            return i
    return 999


def test_rerank_does_not_regress_any_golden(retriever, goldens) -> None:
    """没有任何一条查询被重排弄差——这是敢默认启用重排的前提。"""
    worse = []
    for item in goldens:
        hits = retriever.retrieve_hybrid(item["q"], k=8)["hits"]
        b = _first_rank(hits, set(item["expect"]))
        r = _first_rank(rerank_hits(item["q"], hits), set(item["expect"]))
        if r > b:
            worse.append((item["q"], b, r))
    assert worse == [], f"重排不应让任何查询变差: {worse}"


def test_rerank_keeps_min_at_gate_green(retriever, goldens) -> None:
    ok = 0
    for item in goldens:
        hits = retriever.retrieve_hybrid(item["q"], k=8)["hits"]
        if _first_rank(rerank_hits(item["q"], hits), set(item["expect"])) <= item.get("min_at", 5):
            ok += 1
    assert ok == len(goldens), f"min_at 门禁应全绿: {ok}/{len(goldens)}"


def test_rerank_improves_top1_hit_rate(retriever, goldens) -> None:
    """实测结论固化：top-1 命中率由 12/14 提升到 14/14。

    这条断言把"重排有用"变成回归门禁：今后若改权重把它改差了，测试会响。
    """
    base = rr = 0
    for item in goldens:
        hits = retriever.retrieve_hybrid(item["q"], k=8)["hits"]
        exp = set(item["expect"])
        base += _first_rank(hits, exp) == 1
        rr += _first_rank(rerank_hits(item["q"], hits), exp) == 1
    assert rr >= base, f"重排不应降低 top-1: 基线 {base} → 重排 {rr}"
    assert rr == len(goldens), f"实测应达到全数 top-1，实际 {rr}/{len(goldens)}"
