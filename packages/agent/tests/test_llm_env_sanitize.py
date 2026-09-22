"""环境代理配置的病态输入：一个 `NO_PROXY` 不该让 Agent 用不了 LLM。

## 背景（真实缺陷，2026-09-21 本机实测）

本机 `NO_PROXY=localhost,127.0.0.1,::1,[::1]`。platform 的 `_request()` 有"坏代理
绕过重试"（ADR-024），但 agent 的 LLM 调用走 **langchain-openai 自己的 httpx 客户端**，
根本不经过那层——于是 `tcms-agent run --llm` **在构造客户端阶段就崩**：

    httpx2.InvalidURL: Invalid port: ':1]'

不是"诚实降级"，是直接挂（exit 非零，轨迹里什么都没有）。

修法：构造前把 httpx 认不出的条目剔掉，**并如实告诉用户**——而不是把异常吞掉。
吞掉会让人以为在用 LLM、实际走了别的路，那正是 ADR-024 要根治的症状。

## 平台注意（本文件第一版就栽在这）

Windows 的**环境变量不区分大小写**：`NO_PROXY` 与 `no_proxy` 是同一个变量。
因此：
- 测试里不要 `setenv("NO_PROXY")` 之后再 `delenv("no_proxy")`——那会把刚设的删掉；
- 实现里同时处理两种大小写是**幂等且无害**的（第二次读到的是同一个已修好的值），
  在 POSIX 上则是必要的（不同库读不同大小写）。
"""

from __future__ import annotations

import os

import pytest

from tcms_agent.models import _sanitize_no_proxy_env


def test_bracketed_ipv6_is_removed_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """方括号 IPv6 写法要剔除；不带括号的同义写法必须保留（它是合法的）。"""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")

    note = _sanitize_no_proxy_env()

    assert "[::1]" not in os.environ["NO_PROXY"]
    assert "::1" in os.environ["NO_PROXY"], "合法的 IPv6 写法不该被误删"
    assert "localhost" in os.environ["NO_PROXY"]
    assert "[::1]" in note and "NO_PROXY" in note, f"必须如实说明改了什么，实际 {note!r}"


def test_clean_env_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境本来就是好的 → 一个字都不改、也不报 note（"什么都没做"要看得出来）。"""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")

    assert _sanitize_no_proxy_env() == ""
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1,::1"


def test_repeated_calls_are_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """处理两次与一次等价——Windows 上大小写是同一个变量，实现会被"处理两遍"。"""
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")

    first = _sanitize_no_proxy_env()
    second = _sanitize_no_proxy_env()

    assert first, "第一次应当有东西要修"
    assert second == "", "第二次应当什么都无需做"
    assert os.environ["NO_PROXY"] == "localhost"


def test_why_this_exists_chatopenai_cannot_even_be_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**反向证明**：不清理时，真实的 `ChatOpenAI` **构造**就会失败。

    把"为什么需要这个函数"钉成可执行事实——否则它看起来像一段可有可无的防御代码，
    容易被后人当冗余删掉，而那时症状是"某些机器上 LLM 直接崩"，
    很难联想到是一个环境变量引起的。
    """
    pytest.importorskip("httpx")
    monkeypatch.setenv("NO_PROXY", "[::1]")

    from langchain_openai import ChatOpenAI

    with pytest.raises(Exception, match="Invalid port"):
        ChatOpenAI(model="x", base_url="https://example.com/v1", api_key="k")
