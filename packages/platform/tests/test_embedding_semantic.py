"""P0-2 近义改写 golden：真语义通道 vs 字符哈希的近义增益（**条件执行**）。

口径（诚实，防"橡皮图章"）：
- 只有显式开启 `TCMS_EMBEDDER=api` 且 key/base_url 已配置时才可能运行；
- 未开启 / 无 key / 实际请求失败（自动降级哈希）→ pytest.skip，绝不把
  "哈希余弦"冒充"语义增益"的通过证据；
- 通过条件：近义改写对中，真语义余弦 > 哈希余弦（>0.05 平均边际）且
  ≥80% 单对胜出 —— 证明"近义召回"确实是新通道带来、非旧通道可及。

语料对全部取 TCMS 口语/故障改写（灯闪→照明、受电弓→弓网、门联锁等），
同义项字面重合度低，哈希通道无法靠共享字符取巧。
"""

from __future__ import annotations

import numpy as np
import pytest

from tcms_ai_platform.agent.llm_backend import make_kb_embedder
from tcms_ai_platform.knowledge import HashedEmbedder

NEAR_SYNONYM_PAIRS: list[tuple[str, str]] = [
    ("仪表台灯闪但无故障码", "照明系统异常"),
    ("受电弓离线", "弓网故障"),
    ("车门没关就发车", "门联锁失效"),
    ("心跳报文丢了", "看门狗超时"),
    ("空调不制冷", "制冷压缩机故障"),
]

MIN_BEAT_RATIO = 0.8
MIN_MEAN_MARGIN = 0.05


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a / na, b / nb))


def test_real_embedding_near_synonym_gain_over_hash():
    api = make_kb_embedder()
    if isinstance(api, HashedEmbedder):
        pytest.skip("TCMS_EMBEDDER 未开启或未配置 key —— 真语义通道未激活（降级哈希）")

    hashed = HashedEmbedder()
    margins: list[float] = []
    beats = 0
    for q, doc in NEAR_SYNONYM_PAIRS:
        vq, vd = api.embed(q), api.embed(doc)
        if not api.api_active:
            pytest.skip("真语义通道请求失败并自动降级哈希 —— 本环境无法验证近义增益")
        api_cos = _cos(vq, vd)
        hash_cos = _cos(hashed.embed(q), hashed.embed(doc))
        margins.append(api_cos - hash_cos)
        if api_cos > hash_cos:
            beats += 1

    total = len(NEAR_SYNONYM_PAIRS)
    beat_ratio = beats / total
    mean_margin = float(np.mean(margins))
    assert beat_ratio >= MIN_BEAT_RATIO and mean_margin > MIN_MEAN_MARGIN, (
        f"近义增益不足：单对胜出 {beats}/{total}（<{MIN_BEAT_RATIO}），"
        f"平均余弦边际 {mean_margin:.3f}（≤{MIN_MEAN_MARGIN}）。"
        f"逐对：{dict(zip([q for q, _ in NEAR_SYNONYM_PAIRS], [round(m, 3) for m in margins]))}"
    )
