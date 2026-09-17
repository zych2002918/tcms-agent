"""P2 语义通道端到端验证（配 key 环境；离线时给出诚实引导）。

验证链路：TCMS_EMBEDDER=api + key → make_kb_embedder() → ApiEmbedder（失败自动
降级哈希）→ 对 5 对 TCMS 近义改写计算"真语义余弦 vs 哈希余弦"边际与胜出率。
用途：作为"语义检索可选真通道"的端到端演示/回归，配 key 环境跑出增益数字。

运行：
    set TCMS_EMBEDDER=api && set EMBEDDING_MODEL=text-embedding-v3（可选）
    python scripts/verify_semantic_channel.py
离线/未开启时脚本输出引导并 exit 2（不假装测过）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PAIRS: list[tuple[str, str]] = [
    ("仪表台灯闪但无故障码", "照明系统异常"),
    ("受电弓离线", "弓网故障"),
    ("车门没关就发车", "门联锁失效"),
    ("心跳报文丢了", "看门狗超时"),
    ("空调不制冷", "制冷压缩机故障"),
]


def main() -> int:
    import numpy as np

    from tcms_ai_platform.agent.llm_backend import make_kb_embedder
    from tcms_ai_platform.knowledge import HashedEmbedder

    if not __import__("os").environ.get("TCMS_EMBEDDER", "").strip():
        print("语义通道未开启：设 TCMS_EMBEDDER=api 并配置 key 后运行。\n"
              "说明：默认通道=字符级确定性哈希+BM25+图谱证据（离线）；真语义近义为可选增强。")
        return 2

    api = make_kb_embedder()
    if isinstance(api, HashedEmbedder):
        print("未配置 API key → 已诚实降级哈希。此脚本要求真语义通道，请先配置 key/EMBEDDING_MODEL。")
        return 2

    hashed = HashedEmbedder()

    def cos(a, b):
        na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
        return float(np.dot(a / na, b / nb)) if na and nb else 0.0

    print("语义通道端到端验证（近义改写增益）\n" + "=" * 56)
    beats = 0
    for q, doc in PAIRS:
        vq, vd = api.embed(q), api.embed(doc)
        if not api.api_active:
            print("真语义通道请求失败并自动降级哈希 —— 请检查网络/模型名后重试。")
            return 2
        api_c = cos(vq, vd)
        hash_c = cos(hashed.embed(q), hashed.embed(doc))
        if api_c > hash_c:
            beats += 1
        print(f"{q!r} ~ {doc!r}  真语义 {api_c:.3f} vs 哈希 {hash_c:.3f}  "
              f"{'胜出' if api_c > hash_c else '未胜'}")
    total = len(PAIRS)
    print("=" * 56)
    print(f"单对胜出 {beats}/{total}")
    ok = beats / total >= 0.8
    print("结论：近义改写增益达标（≥80% 胜出）" if ok else "结论：增益不足，请检查模型/提示是否适用于 TCMS 中文口语")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
