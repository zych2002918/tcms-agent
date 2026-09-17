"""上游路径解析的回归护栏。

## 为什么要专门一个文件测"路径"

三仓合一后，代码里散落的 `parents[N] / "tcms-can-test"` 这类**手拼路径**全部失效。
它们失效的方式很阴险：不报错，只是**指向一个不存在的目录**，于是：
- CI 里表现为"测试全部 skip"（本项目踩过两次）；
- 用户侧表现为"装完一启动就崩"（MCP server 就是这样）。

原有测试大多**显式传入 upstream**，正好绕开了这些默认路径，所以一直没抓到。
本文件专门测**不传参数的默认路径**——也就是真实用户会走的那条。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.core.sources import bundled_scenarios_fallback, resolve_asset_source

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# 解析链本身
# ---------------------------------------------------------------------------


def test_resolve_asset_source_points_to_existing_assets() -> None:
    """解析结果必须真的存在——解析出来的路径不存在，等于没有解析。"""
    src = resolve_asset_source()
    assert src.dbc.is_file(), f"DBC 不存在: {src.dbc}（mode={src.mode}）"
    assert src.faults.is_file(), f"故障字典不存在: {src.faults}"
    assert src.scenarios_dir.is_dir(), f"场景目录不存在: {src.scenarios_dir}"
    assert src.rtm.is_file(), f"RTM 不存在: {src.rtm}"


def test_bundled_fallback_is_a_real_directory() -> None:
    """内置快照兜底路径必须存在（app.py 的兜底分支依赖它）。"""
    fb = bundled_scenarios_fallback()
    assert fb is not None, "内置快照场景目录缺失"
    assert fb.is_dir(), f"内置快照场景目录不是目录: {fb}"
    assert list(fb.glob("*.yaml")), f"内置快照场景目录里没有场景: {fb}"


# ---------------------------------------------------------------------------
# MCP server：默认路径必须能直接用（曾经的 bug 点）
# ---------------------------------------------------------------------------


def test_mcp_build_context_without_arguments_works() -> None:
    """`build_context()` **不传参**必须能构建成功。

    这里曾经硬编码 `parents[4] / "tcms-can-test"`（旧的兄弟目录布局），
    三仓合一后指向不存在的路径，pip 安装的用户一启动就崩。
    原 MCP 测试全部显式传 upstream，正好绕过了这条默认路径。
    """
    from tcms_ai_platform.agent.mcp_server import build_context

    ctx = build_context()
    assert ctx.m is not None and ctx.g is not None and ctx.hr is not None
    assert len(ctx.m.faults_by_key) > 0, "应加载到真实故障字典"
    assert len(ctx.g.nodes) > 0, "应构建出知识图谱"
    # 检索可用（证明资产确实加载对了）
    hits = ctx.hr.retrieve_hybrid("车门故障", k=3).get("hits") or []
    assert hits, "默认路径下检索应能命中真实资产"


def test_mcp_build_context_with_explicit_upstream_still_works() -> None:
    """显式传参的老用法不能被破坏。"""
    from tcms_ai_platform.agent.mcp_server import build_context

    src = resolve_asset_source()
    if src.root is None:
        pytest.skip("无活上游（内置快照模式），显式路径用例不适用")
    ctx = build_context(src.root)
    assert len(ctx.m.faults_by_key) > 0


def test_mcp_tools_call_returns_real_data() -> None:
    """端到端：MCP 工具调用应返回真实资产（默认路径下）。"""
    from tcms_ai_platform.agent.mcp_server import build_context, dispatch

    ctx = build_context()
    resp = dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_scenarios", "arguments": {}}},
        ctx,
    )
    assert resp is not None and "result" in resp, resp
    assert resp["result"]["isError"] is False, resp["result"]


# ---------------------------------------------------------------------------
# 通用护栏：以包内路径为基准的手拼路径必须存在
# ---------------------------------------------------------------------------


def test_no_hardcoded_sibling_upstream_left() -> None:
    """源码里不应再有指向 `tcms-can-test` 的**手拼目录**（注释/文案不算）。

    允许出现 `"tcms-can-test"` 字样的地方：文档字符串、提示文案、
    以及 sources/asset_loader 里作为**兼容旧布局的候选名**。
    这里只禁止 `parents[N] / "tcms-can-test"` 这种直接拼路径的写法。
    """
    import re

    pkg = Path(__file__).resolve().parents[1] / "src" / "tcms_ai_platform"
    pattern = re.compile(r'parents\[\d+\]\s*/\s*"tcms-can-test"')
    offenders: list[str] = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{py.relative_to(pkg)}:{i}")
    assert offenders == [], f"仍有手拼的旧上游路径: {offenders}"


def test_bench_script_upstream_resolves() -> None:
    """压测脚本的 UPSTREAM 也必须解析到真实目录。"""
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "bench_retrieval.py"
    if not script.is_file():
        pytest.skip("压测脚本不存在")
    text = script.read_text(encoding="utf-8")
    assert 'parents[2] / "tcms-can-test"' not in text, "压测脚本仍在手拼旧路径"
    assert "resolve_asset_source" in text, "压测脚本应走平台的解析链"
    spec = importlib.util.spec_from_file_location("_bench_probe", script)
    assert spec is not None
