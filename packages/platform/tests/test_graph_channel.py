"""图谱通道：让"三通道融合"从一句宣称变成可逐条核对的事实。

背景：本仓 README 曾声称"BM25 + 向量 + 图谱证据三通道 RRF 融合"，但代码里
`rrf([vec_ids, lex_ids])` 只有两路——图谱只做域路由与命中后富化。这是一处
**措辞超前于实现**的夸大（审计报告里点过名）。

本文件把那条宣称变成真的，并把关键取舍固化成测试：

- 图谱作为**纯补充通道**：只交出文本两路未召回的文档 → 只可能增益，不会稀释；
- 实测依据：若让图谱与文本两路**等权竞争**，本语料下图谱候选 100% 已被文本两路
  覆盖，它不增加召回却把 golden 从 14/14 拉到 12/14。只稀释不增益的通道没有存在理由。
- 图谱的真实价值点在**症状式查询**（无实体名、文本重合低）上，见
  `test_graph_channel_adds_recall_for_symptom_query`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.domain import enrich_graph
from tcms_ai_platform.knowledge import (
    HybridRetriever,
    VectorStore,
    build_docs_from_asset,
    build_knowledge_graph,
)
from tcms_ai_platform.knowledge.lexical import rrf

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"  # monorepo: packages/engine
NEEDS_UPSTREAM = pytest.mark.skipif(not UPSTREAM.is_dir(), reason=f"上游不存在: {UPSTREAM}")


@pytest.fixture(scope="module")
def hr():
    """**按生产配置**构建检索器：create_app 会调用 enrich_graph 注入领域知识
    （症状/因果/系统域），图谱通道依赖这些边。只建图不注入会得到与线上不一致的行为。
    """
    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    store = VectorStore()
    store.add_many(build_docs_from_asset(m))
    enrich_graph(g, store)
    return HybridRetriever(store, g)


# ---------------------------------------------------------------------------
# rrf 权重支持
# ---------------------------------------------------------------------------


def test_rrf_default_is_equal_weight() -> None:
    """等权是默认也是纪律：没有标定数据就不臆造权重。"""
    a, b = ["x", "y"], ["y", "z"]
    assert rrf([a, b], top=3) == rrf([a, b], top=3, weights=[1.0, 1.0])
    got = dict(rrf([a, b], top=3))
    assert abs(got["y"] - (1 / 62 + 1 / 61)) < 1e-9, "两路都命中的应拿到两份贡献"


def test_rrf_weights_downweight_a_channel() -> None:
    a, b = ["x"], ["y"]
    assert [d for d, _ in rrf([a, b], top=2)] == ["x", "y"]
    # y 权重抬高后应超过 x
    assert [d for d, _ in rrf([a, b], top=2, weights=[1.0, 2.0])] == ["y", "x"]


def test_rrf_weights_length_must_match() -> None:
    try:
        rrf([["a"], ["b"]], weights=[1.0])
    except ValueError as e:
        assert "数量" in str(e)
    else:  # pragma: no cover
        raise AssertionError("权重数量不匹配必须报错，不能静默截断")


# ---------------------------------------------------------------------------
# doc_id ↔ 图谱节点 id 的别名
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_requirement_alias_resolves_both_ways(hr) -> None:
    # 需求文档用 req: 前缀，图谱节点用 requirement:
    assert "req:SR-21" in hr._docs_map_of()
    assert hr._to_node_id("req:SR-21") == "requirement:SR-21"
    assert hr._to_doc_id("requirement:SR-21", hr._docs_map_of()) == "req:SR-21"


@NEEDS_UPSTREAM
def test_unknown_id_returns_none_not_a_guess(hr) -> None:
    assert hr._to_node_id("fault:definitely_not_real") is None, "找不到就返回 None，不许猜"
    assert hr._to_doc_id("fault:definitely_not_real", hr._docs_map_of()) is None


@NEEDS_UPSTREAM
def test_same_id_needs_no_alias(hr) -> None:
    assert hr._to_node_id("fault:door_fault") == "fault:door_fault"


# ---------------------------------------------------------------------------
# 纯补充语义（本通道的核心约束）
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_graph_channel_never_returns_known_docs(hr) -> None:
    """纯补充：已召回的一律不出现在图谱通道里——这是"不稀释"的机制保证。"""
    docs = hr._docs_map_of()
    known = {"fault:door_fault", "system:SYS-DOOR", "function:F-DOOR"}
    got = hr._graph_channel(["fault:door_fault"], known, limit=50)
    assert got, "应能扩出邻居"
    assert not (set(got) & known), f"图谱通道不应返回已召回文档: {set(got) & known}"
    assert all(d in docs for d in got), "只返回真实存在的文档"


@NEEDS_UPSTREAM
def test_graph_channel_is_deterministic(hr) -> None:
    a = hr._graph_channel(["fault:door_fault"], set(), limit=10)
    b = hr._graph_channel(["fault:door_fault"], set(), limit=10)
    assert a == b


def test_graph_channel_empty_graph_is_safe() -> None:
    from tcms_ai_platform.knowledge.retriever import HybridRetriever
    from tcms_ai_platform.knowledge.vector import VectorStore

    class _EmptyGraph:
        nodes: dict = {}

        def neighbors(self, nid):  # pragma: no cover - 不会被调用
            return []

    r = HybridRetriever(VectorStore(), _EmptyGraph())  # type: ignore[arg-type]
    assert r._graph_channel(["fault:x"], set()) == [], "空图应诚实返回空，不抛错"


# ---------------------------------------------------------------------------
# 融合效果（实测行为固化）
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_hits_carry_channel_annotation(hr) -> None:
    """每条命中都要标注"被哪几路召回"——这是"三通道"可逐条核对的前提。"""
    hits = hr.retrieve_hybrid("车门故障 不能发车", k=5)["hits"]
    assert hits
    for h in hits:
        assert "channels" in h, f"命中缺少 channels 标注: {h.get('doc_id')}"
        assert h["channels"], "至少有一路召回才有意义"
        assert set(h["channels"]) <= {"vector", "lexical", "graph"}


@NEEDS_UPSTREAM
def test_graph_channel_adds_recall_for_symptom_query(hr) -> None:
    """图谱通道的**真实价值点**：症状式查询（无实体名、文本重合低）。

    这类查询在文本两路下只能靠字面巧合命中；图谱能沿"症状 → 系统/功能/故障"
    的边把结构相邻的资产捞出来。反之，带实体名的查询（如「车门故障」）文本两路
    已经足够，图谱补不出新东西——这两种情况的差异是实测结论，不是设计预期。
    """
    hits = hr.retrieve_hybrid("仪表盘闪烁", k=8)["hits"]
    graph_only = [h["doc_id"] for h in hits if set(h["channels"]) == {"graph"}]
    assert graph_only, "症状式查询应能拿到仅图谱召回的补充文档"


@NEEDS_UPSTREAM
def test_entity_named_query_needs_no_graph_supplement(hr) -> None:
    """对照：带实体名的查询，图谱通道补不出新文档（诚实记录这一事实）。"""
    hits = hr.retrieve_hybrid("车门故障 不能发车", k=8)["hits"]
    graph_only = [h["doc_id"] for h in hits if set(h["channels"]) == {"graph"}]
    assert graph_only == [], f"本语料下该查询不需要图谱补充，实际: {graph_only}"
