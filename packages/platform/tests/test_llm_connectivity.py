"""LLM 连通性：**配了 key ≠ 能用**——这条测试与那个端点都是为它而写。

## 真实事故（本机实测，不是假想）

设置里 key 配了、模型名写着 `deepseek-v4-pro-0813`，界面上显示"LLM 已配置"，
但平台**每一次** LLM 调用都在失败，一直在静默走离线规则臂。
根因在环境而不在代码：`NO_PROXY=localhost,127.0.0.1,::1,[::1]` 里的 `[::1]`
让 httpx 在**建 URL 阶段**就抛 `InvalidURL: Invalid port: ':1]'`。

发现它的是白盒：报告里那句 `[LLM 不可用，已落回确定性]`。
但"要用户自己从轨迹里读出来"不够——于是有了 `/api/llm/ping`
（自检：我选的这个模型现在通不通）与请求层的**绕过坏代理重试**。

这里守三件事：
1. 坏代理不该把功能整体废掉（重试一次，且**说明**做过什么）；
2. 自检端点要如实回错误原文，不许美化成功；
3. 无 key 时自检要明确说"没配 key"，而不是含糊失败。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tcms_ai_platform.agent.llm_backend import _is_env_proxy_error, _request, ping_model
from tcms_ai_platform.server.app import create_app

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def client():
    if not UPSTREAM.is_dir():
        pytest.skip(f"上游不存在: {UPSTREAM}")
    from fastapi.testclient import TestClient

    return TestClient(create_app(upstream=UPSTREAM))


# ---------------------------------------------------------------------------
# 1. 坏掉的代理配置：识别 + 绕过重试 + 说明
# ---------------------------------------------------------------------------


def test_proxy_config_error_is_recognized():
    """`[::1]` 那种 NO_PROXY 报出来的正是 httpx.InvalidURL（实测原文）。"""
    e = httpx.InvalidURL("Invalid port: ':1]'")
    assert _is_env_proxy_error(e) is True
    assert _is_env_proxy_error(RuntimeError("InvalidURL: Invalid port: ':1]'")) is True
    # 普通网络错误不该被误判成"代理配置坏"（否则会白白绕过代理）
    assert _is_env_proxy_error(httpx.ConnectTimeout("timeout")) is False
    assert _is_env_proxy_error(ValueError("别的错")) is False


def test_request_bypasses_broken_proxy_and_says_so(monkeypatch):
    """第一次因坏代理失败 → 绕过代理（trust_env=False）重试，并回报说明。"""
    calls: list[bool] = []

    def fake_request(method, url, **kw):  # noqa: ARG001
        calls.append(True)
        raise httpx.InvalidURL("Invalid port: ':1]'")

    class FakeClient:
        def __init__(self, *a, **kw):
            self.trust_env = kw.get("trust_env")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):  # noqa: ARG002
            calls.append(self.trust_env is False)
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "request", fake_request)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    r, note = _request("POST", "https://example.invalid/v1/chat/completions", json={})
    assert r is not None and r.status_code == 200, "绕过代理后应成功"
    assert calls == [True, True], "应当重试一次（且重试时 trust_env=False）"
    assert "绕过代理" in note, f"必须说明做过什么，实际：{note!r}"


def test_request_reports_failure_instead_of_raising(monkeypatch):
    """绕过代理仍失败 → 返回 (None, 原因)，由上层如实降级（不抛给用户）。"""

    def boom(*a, **kw):  # noqa: ARG001
        raise httpx.InvalidURL("Invalid port: ':1]'")

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **kw):  # noqa: ARG002
            raise httpx.ConnectError("网络不可达")

    monkeypatch.setattr(httpx, "request", boom)
    monkeypatch.setattr(httpx, "Client", FakeClient)
    r, note = _request("GET", "https://example.invalid/models")
    assert r is None
    assert "仍失败" in note and "网络不可达" in note


# ---------------------------------------------------------------------------
# 2. 自检：如实（成功就是成功，失败给原文）
# ---------------------------------------------------------------------------


def test_ping_without_key_says_so(monkeypatch):
    """没配 key 时说清"没配 key"，而不是含糊的"连接失败"。"""
    from tcms_ai_platform.agent import llm_backend

    monkeypatch.setattr(llm_backend, "_api_key", lambda: None)
    res = ping_model(model="whatever")
    assert res["ok"] is False
    assert "未配置 API key" in res["error"]
    assert res["model"] == "whatever"


def test_ping_success_reports_model_and_latency(monkeypatch):
    from tcms_ai_platform.agent import llm_backend

    monkeypatch.setattr(llm_backend, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        llm_backend,
        "_request",
        lambda *a, **kw: (httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]}), ""),
    )
    res = ping_model(model="tiny")
    assert res["ok"] is True and res["model"] == "tiny"
    assert res["latency_ms"] is not None and res["error"] is None


def test_ping_failure_keeps_server_error_verbatim(monkeypatch):
    """失败必须回**服务端原文**（状态码 + 响应片段），不许美化成功。"""
    from tcms_ai_platform.agent import llm_backend

    monkeypatch.setattr(llm_backend, "_api_key", lambda: "sk-bad")
    monkeypatch.setattr(
        llm_backend,
        "_request",
        lambda *a, **kw: (httpx.Response(400, text='{"error":{"message":"model not found"}}'), ""),
    )
    res = ping_model(model="nope")
    assert res["ok"] is False
    assert "HTTP 400" in res["error"] and "model not found" in res["error"]


@NEEDS_UPSTREAM
def test_ping_endpoint_shape(client, monkeypatch):
    """端点契约：ok/model/base_url/latency_ms/error/note 六个字段都在。"""
    from tcms_ai_platform.agent import llm_backend

    monkeypatch.setattr(llm_backend, "_api_key", lambda: None)
    r = client.post("/api/llm/ping", json={"model": "x"})
    assert r.status_code == 200
    d = r.json()
    assert set(d) == {"ok", "model", "base_url", "latency_ms", "error", "note"}
    assert d["ok"] is False and "未配置 API key" in d["error"]
