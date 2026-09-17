"""词法检索层：字符/词 BM25（IDF × 饱和词频），与向量余弦互补。

动机（P1-1）：向量通道默认是字符级哈希（无近义能力，只重字面/近形；
真语义近义需显式启用 ApiEmbedder / 本地 BGE 等 Embedder 实现），因此对
“精确故障键/信号名/中文术语”的命中主要靠词法（词频 + IDF），更稳也可解释；
两者 rank 融合（RRF）同时保住近义（真语义通道启用时）与字面精确，且
**完全离线、确定性、可单测**。

- tokenizer 与 vector._tokens 同口径（中文按字 + 英文/数字按词），保证两通道同一语料。
- BM25Index 一次性建库（doc 数少，内存小）；查询时先按 domain 过滤语料（域分区一致）。
- 真语义通道即 Embedder 接口的另一种实现（见 vector.ApiEmbedder），本层不变。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# 与 vector._tokens 同口径的轻量切分（中文按字 + 英文/数字按词；单一真源）
_TOK_WORDS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中文按字 + 英文/数字按词（与 vector._tokens 同口径，顺序不影响打分）。"""
    low = (text or "").lower()
    words = _TOK_WORDS.findall(low)
    chars = _CJK.findall(low)
    return words + chars


@dataclass(frozen=True)
class LexDoc:
    doc_id: str
    text: str
    domain: str = ""


class BM25Index:
    """轻量 BM25（k1=1.5, b=0.75）；只读建库后查询线程安全（无状态更新）。"""

    def __init__(self, docs: list[LexDoc], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[LexDoc] = list(docs)
        self._doc_len = [len(tokenize(d.text)) for d in self._docs]
        n = len(self._docs)
        self._avgdl = (sum(self._doc_len) / n) if n else 0.0
        self._df: dict[str, int] = {}
        self._tfs: list[dict[str, int]] = []
        for di, doc in enumerate(self._docs):
            tf: dict[str, int] = {}
            for tok in tokenize(doc.text):
                tf[tok] = tf.get(tok, 0) + 1
            self._tfs.append(tf)
            for tok in tf:
                self._df[tok] = self._df.get(tok, 0) + 1
        self._idf = {
            tok: math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for tok, df in self._df.items()
        }

    def _candidates(self, domain: str | None) -> list[int]:
        if not domain:
            return list(range(len(self._docs)))
        return [i for i, d in enumerate(self._docs) if d.domain == domain]

    def score_all(self, query: str, domain: str | None = None) -> dict[str, float]:
        """全部候选文档的 BM25 分（doc_id → score）。"""
        terms = tokenize(query)
        idxs = self._candidates(domain)
        out: dict[str, float] = {}
        if not terms or not idxs:
            return out
        for i in idxs:
            s = 0.0
            dl = self._doc_len[i] or 1
            tfmap = self._tfs[i]
            for tok in terms:
                idf = self._idf.get(tok, 0.0)
                if idf <= 0:
                    continue
                tf = tfmap.get(tok, 0)
                if tf <= 0:
                    continue
                s += idf * (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            if s > 0:
                out[self._docs[i].doc_id] = s
        return out

    def top(self, query: str, k: int = 8, domain: str | None = None) -> list[tuple[str, float]]:
        scores = self.score_all(query, domain=domain)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:k]


# ---------------------------------------------------------------------------
# Rank 融合（RRF：与具体分数无关，稳健、可解释）
# ---------------------------------------------------------------------------


def rrf(rankings: list[list[str]], k: int = 60, top: int = 8) -> list[tuple[str, float]]:
    """多路排序 → RRF 融合分数；返回 top 个 (doc_id, rrf_score)（降序、id 决胜）。"""
    acc: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            acc[doc_id] = acc.get(doc_id, 0.0) + 1.0 / (k + rank)
    fused = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))
    return fused[:top]


def normalize_rank_scores(scores: dict[str, float]) -> dict[str, float]:
    """min-max 归一化到 [0,1]（空 → 空）。"""
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    if hi <= lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}
