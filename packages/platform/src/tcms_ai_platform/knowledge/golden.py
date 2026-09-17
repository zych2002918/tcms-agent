"""检索评测（P1-1）：golden queries → hits@k / 命中位置 / 通过率。

用途：任何检索改动（向量通道/词法通道/融合参数/路由）都以本评测集为门禁，
防止“单点示例调好了、别处回退”的隐性漂移 —— 机器自证检索质量。

数据：domain/data/retrieval_golden.yaml（随包分发，expect 全是真实资产 doc_id）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .retriever import HybridRetriever

GOLDEN_FILE = Path(__file__).resolve().parent.parent / "domain" / "data" / "retrieval_golden.yaml"


def load_golden() -> list[dict]:
    if not GOLDEN_FILE.is_file():
        return []
    data = yaml.safe_load(GOLDEN_FILE.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


def evaluate_retriever(
    retriever: HybridRetriever,
    goldens: list[dict] | None = None,
    k: int = 5,
    hybrid: bool = True,
) -> dict:
    """跑一遍 golden：返回 {total, passed, pass_rate, top1_hits, rows}。

    row：{q, expect, top:[doc_id...], min_at, pass}；pass = 期望项在 top-k 内且
    （有 min_at 时）首次命中位置 ≤ min_at。
    """
    goldens = goldens if goldens is not None else load_golden()
    fn = retriever.retrieve_hybrid if hybrid else retriever.retrieve
    rows = []
    passed = top1 = 0
    for g in goldens:
        q = g["q"]
        expect = list(g.get("expect", []))
        resp = fn(q, k=k)
        top = [h["doc_id"] for h in resp.get("hits", [])]
        pos = next((i for i, d in enumerate(top, start=1) if d in expect), None)
        min_at = g.get("min_at")
        ok = pos is not None and (min_at is None or pos <= min_at)
        if ok:
            passed += 1
        if pos == 1:
            top1 += 1
        rows.append({"q": q, "expect": expect, "top": top[:k], "min_at": min_at, "pass": ok, "pos": pos})
    total = len(goldens)
    return {
        "total": total,
        "passed": passed,
        "top1": top1,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "rows": rows,
    }
