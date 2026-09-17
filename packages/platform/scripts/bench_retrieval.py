"""P2-4 检索规模退化压测：语料 ×1/×2/×5/×10 下 top-k 一致性 + 查询耗时。

用途：拷问"文档×10、×100，top-k 还稳吗？每查询多少 ms？"的机器答案。
方法（诚实口径）：
- 语料 = 真实 enrich 知识库；按倍率复制文档（原 doc_id 保持在头部，副本带
  `#{r}` 后缀，仅在尾部追加）→ 原 golden 期望 id 的位置不因副本前插而劣化，
  一致性对比的是真实 top-k 行为随规模的变化，不是凑数。
- 指标：
  * golden 门禁（混合通道）是否仍全过（14/14 防回退）；
  * 平均单查询耗时（ms，含向量+BM25+RRF+路由）；
  * top-k 一致性 = 各倍率相对 ×1 的 top5 平均重合率（Jaccard）。
- 观察点：若重合率断崖或耗时非线性暴涨 → 该引入 ANN（hnswlib）抽象实现；
  若平稳 → 现有接口抽象已够（10^5 量级再换）。

运行：.venv\\Scripts\\python scripts/bench_retrieval.py [--factors 1 2 5 10] [--queries 20]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
ROOT = Path(__file__).resolve().parent.parent

from tcms_ai_platform.core import load_asset_model  # noqa: E402
from tcms_ai_platform.domain import enrich_graph  # noqa: E402
from tcms_ai_platform.knowledge import (  # noqa: E402
    Doc,
    HybridRetriever,
    KnowledgeGraph,
    VectorStore,
    build_docs_from_asset,
    build_knowledge_graph,
)
from tcms_ai_platform.knowledge.golden import evaluate_retriever, load_golden  # noqa: E402

_EXTRA_QUERIES = [
    "仪表盘闪烁但无故障码",
    "SOC 跳变",
    "车门没关就发车",
    "受电弓离线",
    "客室灯组频闪",
    "网络时断时续",
]


def build_base() -> tuple[list[Doc], list[dict], KnowledgeGraph]:
    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    enrich_graph(g, vs)
    docs = list(vs.docs)
    goldens = load_golden()
    return docs, goldens, g


def scale_docs(docs: list[Doc], factor: int) -> list[Doc]:
    """按倍率复制语料：原 doc 在前（保持 golden 期望位），副本带 #{r} 后缀追加。"""
    if factor <= 1:
        return list(docs)
    out = list(docs)
    for r in range(1, factor):
        for d in docs:
            meta = dict(d.meta)
            out.append(Doc(doc_id=f"{d.doc_id}#{r}", kind=d.kind, text=d.text, meta=meta))
    return out


def build_retriever(docs: list[Doc], g: KnowledgeGraph) -> HybridRetriever:
    vs = VectorStore()
    vs.add_many(docs)
    # 副本节点不在图：检索走同一路由/BM25/向量代码路径即可（规模是唯一变量）
    return HybridRetriever(vs, g)


def bench(factor: int, docs: list[Doc], queries: list[str], g: KnowledgeGraph, k: int = 5, warm: int = 3) -> dict:
    hr = build_retriever(scale_docs(docs, factor), g)
    for q in queries[:warm]:
        hr.retrieve_hybrid(q, k=k)  # 预热 BM25 懒建与热路径
    t0 = time.perf_counter()
    tops: list[list[str]] = []
    for q in queries:
        tops.append([h["doc_id"] for h in hr.retrieve_hybrid(q, k=k)["hits"]])
    per_q_ms = (time.perf_counter() - t0) / len(queries) * 1000.0
    gate = evaluate_retriever(hr, goldens=load_golden(), k=k, hybrid=True)
    return {"factor": factor, "docs": len(scale_docs(docs, factor)), "per_query_ms": per_q_ms,
            "gate": f"{gate['passed']}/{gate['total']}", "tops": tops}


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def main(factors: list[int], n_queries: int) -> None:
    docs, _goldens, g = build_base()
    if not docs:
        print("语料为空（需上游 tcms-can-test）", file=sys.stderr)
        return 1
    goldens = load_golden()
    queries = [x["q"] for x in goldens] + _EXTRA_QUERIES
    queries = queries[:n_queries]
    results = [bench(f, docs, queries, g) for f in factors]
    base = results[0]["tops"]
    print("=" * 72)
    print(f"检索规模退化压测（{len(queries)} 条查询 × top5，语料基数 {len(docs)}）")
    print("=" * 72)
    print(f"{'x 倍率':>6} {'文档数':>7} {'每查询ms':>9} {'golden门禁':>9} {'top5重合率':>9}")
    for r in results:
        overlap = sum(_jaccard(a, b) for a, b in zip(base, r["tops"])) / len(base)
        print(
            f"{r['factor']:>6}× {r['docs']:>7} {r['per_query_ms']:>8.2f} "
            f"{r['gate']:>9} {overlap:>8.1%}"
        )
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--factors", nargs="+", type=int, default=[1, 2, 5, 10])
    ap.add_argument("--queries", type=int, default=20)
    args = ap.parse_args()
    raise SystemExit(main(args.factors, args.queries))
