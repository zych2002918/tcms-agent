"""首帧主题：把服务端已知的主题写进 HTML，消掉"闪一下再跳主题"。

背景（真实可见的缺陷）：`index.html` 里的防闪脚本只能读 **localStorage**，
而用户真正选定的主题存在**服务端设置**里。换浏览器 / 清过缓存 / 两者不一致时，
页面会先按系统主题画一帧，再跳到用户主题——深色↔浅色那一下非常刺眼，
而且偏偏发生在"第一次打开"这个最需要可信感的时刻。

做法：服务端渲染 index.html 时注入 `<html class="theme-x" data-theme="x">`，
前端脚本看到 data-theme 就直接沿用，不再自己猜。

这里守两件事：注入要对（两种主题都对），以及**没设主题时不许乱注入**
（否则会把未选择的用户强行锁进某个主题）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


@NEEDS_UPSTREAM
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_index_html_carries_the_server_theme(client, monkeypatch, tmp_path, theme):
    """设置里选的主题必须出现在首帧 HTML 的 <html> 上。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    assert client.post("/api/settings", json={"theme": theme}).status_code == 200
    r = client.get("/agent")
    assert r.status_code == 200
    assert f'class="theme-{theme}"' in r.text, "首帧主题没有注入 → 会闪一下再跳"
    assert f'data-theme="{theme}"' in r.text, "前端脚本要靠 data-theme 沿用服务端选择"


@NEEDS_UPSTREAM
def test_unset_theme_injects_nothing(client, monkeypatch, tmp_path):
    """没设过主题时不许注入：否则会把"跟随系统/本地选择"的用户锁进某一主题。"""
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path))
    r = client.get("/")
    head = r.text.split("<body", 1)[0]
    assert 'class="theme-dark"' not in head
    assert 'class="theme-light"' not in head


@NEEDS_UPSTREAM
def test_frontend_script_prefers_server_theme(client):
    """前端脚本必须**先看** data-theme 再退回 localStorage/系统偏好（顺序即优先级）。"""
    html = client.get("/").text
    assert "el.dataset.theme" in html
    i_server = html.index("el.dataset.theme")
    i_local = html.index("localStorage.getItem")
    assert i_server < i_local, "服务端主题必须先于 localStorage 被读取"


@NEEDS_UPSTREAM
def test_entry_html_is_never_cached(client):
    """入口 HTML 必须 `no-store`：否则用户会一直加载上一版的哈希资源。

    这是"我明明重启了，界面还是旧的"最常见的原因之一——SPA 外壳里写的是
    带内容哈希的 JS/CSS 文件名，外壳被缓存 = 永远指向已经不存在的旧资源。
    哈希资源本身可以长期缓存，唯独入口不行。
    """
    r = client.get("/agent")
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc, f"入口 HTML 缺 no-store，实际：{cc!r}"
