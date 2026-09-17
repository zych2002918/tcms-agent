"""P0-2 ApiEmbedder 单测（全离线：httpx MockTransport 假端点）。

覆盖（诚实降级路径）：
- 有 key/model → 真调 /embeddings，向量来自 API；
- HTTP 错误 / 无 key / 模型自动探测失败 → 自动落回 HashedEmbedder（形状与
  数值与哈希一致），绝不因网络抖动崩溃；
- VectorStore.add_many 走 embed_batch（API 一次批量请求，哈希逐条等价）。
"""

from __future__ import annotations

import json

import httpx
import numpy as np

from tcms_ai_platform.knowledge import ApiEmbedder, Doc, HashedEmbedder, VectorStore


def _fake_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _vec(text: str, n: int) -> list[float]:
    """确定性假向量（按字符哈希进 n 维），让测试可断言但不依赖真实模型。"""
    import hashlib

    out = [0.0] * n
    for ch in text:
        out[int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16) % n] += 1.0
    norm = np.linalg.norm(out)
    return [v / norm for v in out] if norm else out


def test_api_embedder_calls_embeddings_endpoint():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"data": [{"embedding": _vec(t, 4)} for t in body["input"]]}
        )

    emb = ApiEmbedder(
        base_url="https://fake.local/v1",
        api_key="test-key",
        model="text-embedding-v3",
        client=_fake_client(handler),
    )
    v = emb.embed("灯闪")
    assert v.shape == (4,)
    assert emb.api_active is True
    assert calls, "未发出任何 HTTP 请求"
    assert calls[-1].url.path.endswith("/embeddings")
    body = json.loads(calls[-1].content)
    assert body["model"] == "text-embedding-v3"
    assert body["input"] == ["灯闪"]


def test_api_embedder_embed_batch_single_request():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": _vec(t, 4)} for t in body["input"]
                ]
            },
        )

    emb = ApiEmbedder(
        base_url="https://fake.local/v1",
        api_key="test-key",
        model="text-embedding-v3",
        client=_fake_client(handler),
    )
    out = emb.embed_batch(["灯闪", "照明异常", "受电弓"])
    assert len(out) == 3 and all(v.shape == (4,) for v in out)
    assert len(calls) == 1, "批量文本应只发一次 /embeddings 请求"
    assert emb.api_active is True


def test_api_embedder_http_error_falls_back_to_hash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    emb = ApiEmbedder(
        base_url="https://fake.local/v1",
        api_key="test-key",
        model="text-embedding-v3",
        client=_fake_client(handler),
    )
    v = emb.embed("灯闪")
    np.testing.assert_allclose(v, HashedEmbedder().embed("灯闪"), rtol=1e-6)
    assert emb.api_active is False, "HTTP 失败必须标记降级（供语义 golden 判断）"


def test_api_embedder_without_key_is_pure_hash():
    emb = ApiEmbedder()
    np.testing.assert_allclose(emb.embed("灯闪"), HashedEmbedder().embed("灯闪"), rtol=1e-6)
    assert emb.api_active is False


def test_api_embedder_autodetect_embedding_model():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "deepseek-chat", "owned_by": "deepseek"},
                        {"id": "text-embedding-v3", "owned_by": "system"},
                    ]
                },
            )
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"embedding": _vec(t, 8)} for t in body["input"]]},
        )

    emb = ApiEmbedder(
        base_url="https://fake.local/v1", api_key="test-key", client=_fake_client(handler)
    )
    v = emb.embed("灯闪")
    assert v.shape == (8,), "应自动探测到 embedding 模型并走真通道"
    assert emb.api_active is True


def test_api_embedder_model_probe_failure_falls_back():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no /models")

    emb = ApiEmbedder(
        base_url="https://fake.local/v1", api_key="test-key", client=_fake_client(handler)
    )
    v = emb.embed("灯闪")
    np.testing.assert_allclose(v, HashedEmbedder().embed("灯闪"), rtol=1e-6)
    assert emb.api_active is False


class _CountingEmbedder(HashedEmbedder):
    """记录 embed / embed_batch 调用次数，验证 VectorStore 走批量路径。"""

    def __init__(self) -> None:
        super().__init__()
        self.embed_calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> np.ndarray:  # type: ignore[override]
        self.embed_calls += 1
        return super().embed(text)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        self.batch_calls += 1
        return super().embed_batch(texts)


def test_vectorstore_add_many_uses_embed_batch():
    c = _CountingEmbedder()
    vs = VectorStore(embedder=c)
    ok = vs.add_many(
        [
            Doc("fault:a", "fault", "灯闪 照明异常"),
            Doc("fault:b", "fault", "受电弓离线"),
            Doc("fault:c", "fault", "车门未关闭"),
        ]
    )
    assert ok == 3
    assert vs.stats()["docs"] == 3
    assert c.batch_calls == 1, "add_many 应调用一次 embed_batch"
    assert c.embed_calls == 3, "哈希批量路径内部按条 embed（等价逐条）"


def test_vectorstore_add_many_respects_partition_caps():
    """批量路径不破坏分区上限语义（拒绝不越界、计数正确）。"""
    vs = VectorStore(partition_caps={"door": 1})
    docs = [
        Doc("fault:a", "fault", "车门未关闭", meta={"domain": "door"}),
        Doc("fault:b", "fault", "车门再故障", meta={"domain": "door"}),
        Doc("fault:c", "fault", "灯闪", meta={"domain": "light"}),
    ]
    ok = vs.add_many(docs)
    assert ok == 2
    ids = {d.doc_id for d in vs.docs}
    assert ids == {"fault:a", "fault:c"}
