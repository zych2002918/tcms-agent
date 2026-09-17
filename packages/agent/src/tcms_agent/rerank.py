"""重排（rerank）：在融合结果之上按**可解释特征**重新排序。

## 为什么重排、以及为什么不用模型

融合阶段（RRF）只用**排名**，丢掉了"这条为什么被召回"的信息。重排把这些信息捡回来：

    特征                含义                                  方向
    ------------------  ------------------------------------  --------
    channel_consensus   被几路召回（1~3）                      越多越可信
    query_overlap       查询字符在文档文本中的覆盖率            越高越相关
    id_hit              查询词是否**字面出现**在 doc_id 里      强信号
    kind_prior          资产类型先验（fault/req/scenario 等）    测试场景下的经验偏好

**不用 LLM 重排**的三个理由：
1. LLM 重排不可离线复现，会让检索质量无法进回归门禁；
2. 本项目已有"LLM 只能在候选内重排、不得引入新文档"的纪律（见 diagnoser），
   在检索这一层引入 LLM 只会把不确定性带进最底层；
3. 特征权重可以被**评测数据标定**——这正是 R7 要做的事，比拍脑袋调 prompt 靠谱。

权重与特征全部显式写在 `FEATURES` 里，且每条结果都回传 `features` 明细，
使"为什么这条被排到前面"可逐条核对。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 测试工程场景下的资产类型先验。取值是经验值，**不是标定值**——
#: 之所以敢写出来，是因为它们只做微调（±0.05 量级），不会主导排序。
KIND_PRIOR: dict[str, float] = {
    "fault": 0.05,
    "requirement": 0.04,
    "scenario": 0.03,
    "symptom": 0.03,
    "function": 0.02,
    "system": 0.02,
}

#: 特征权重（显式、可测、可被 R7 的评测改写）
FEATURES: dict[str, float] = {
    "channel_consensus": 0.45,
    "query_overlap": 0.35,
    "id_hit": 0.15,
    "kind_prior": 0.05,
}


@dataclass
class RerankFeatures:
    channel_consensus: float
    query_overlap: float
    id_hit: float
    kind_prior: float

    def to_dict(self) -> dict[str, float]:
        return {
            "channel_consensus": round(self.channel_consensus, 4),
            "query_overlap": round(self.query_overlap, 4),
            "id_hit": round(self.id_hit, 4),
            "kind_prior": round(self.kind_prior, 4),
        }


def _cjk_and_words(text: str) -> set[str]:
    """中文按字、拉丁按词切分（与知识底座的字符级口径一致，确定性）。"""
    import re

    chars = set(re.findall(r"[\u4e00-\u9fff]", text or ""))
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text or "")}
    return chars | words


def compute_features(query: str, hit: dict[str, Any]) -> RerankFeatures:
    """计算单条命中的特征（全部确定性、无外部依赖）。"""
    channels = hit.get("channels") or []
    consensus = min(len(channels), 3) / 3.0

    q = _cjk_and_words(query)
    text = _cjk_and_words(str(hit.get("text") or ""))
    overlap = (len(q & text) / len(q)) if q else 0.0

    doc_id = str(hit.get("doc_id") or "").lower()
    id_hit = 1.0 if any(tok and tok in doc_id for tok in q) else 0.0

    prior = KIND_PRIOR.get(str(hit.get("kind") or ""), 0.0)

    return RerankFeatures(
        channel_consensus=consensus,
        query_overlap=overlap,
        id_hit=id_hit,
        kind_prior=prior,
    )


def rerank_score(hit: dict[str, Any], feats: RerankFeatures) -> float:
    return (
        FEATURES["channel_consensus"] * feats.channel_consensus
        + FEATURES["query_overlap"] * feats.query_overlap
        + FEATURES["id_hit"] * feats.id_hit
        + FEATURES["kind_prior"] * feats.kind_prior
    )


def rerank_hits(
    query: str, hits: list[dict[str, Any]], *, top_k: int | None = None
) -> list[dict[str, Any]]:
    """按特征分重排（**只重排、不增删**——与"LLM 只能在候选内重排"同一纪律）。

    返回新的列表（原 dict 复制后附加 `rerank_score` / `features` / `rrf_rank`），
    便于对照"重排前第几名、重排后第几名"。
    """
    out: list[dict[str, Any]] = []
    for i, h in enumerate(hits):
        feats = compute_features(query, h)
        item = dict(h)
        item["rrf_rank"] = i + 1
        item["rerank_score"] = round(rerank_score(h, feats), 6)
        item["features"] = feats.to_dict()
        out.append(item)
    out.sort(key=lambda x: (-x["rerank_score"], x["rrf_rank"]))
    return out[:top_k] if top_k else out


def apply_to_kb_result(query: str, result: dict[str, Any]) -> dict[str, Any]:
    """把重排应用到知识底座检索结果上（供 Agent 的工具层调用）。

    保持返回结构兼容（仍是 `hits`），只增加重排标注与顺序变化，
    并显式写入 `reranked: true` 让下游能分辨"这条结果被重排过"。
    """
    hits = result.get("hits") or []
    if not isinstance(hits, list) or not hits:
        return result
    out = dict(result)
    out["hits"] = rerank_hits(query, hits)
    out["reranked"] = True
    out["rerank_note"] = "按可解释特征重排（只重排不增删）；features 字段给出每项得分明细"
    return out


__all__ = [
    "FEATURES",
    "KIND_PRIOR",
    "RerankFeatures",
    "apply_to_kb_result",
    "compute_features",
    "rerank_hits",
    "rerank_score",
]
