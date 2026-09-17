"""P1-b MCP server 门禁：协议子集 + 工具执行 + 诚实错误 + stdio 端到端。

- dispatch 覆盖 initialize/tools/list/tools/call/ping/未知方法/坏 JSON(-32700)。
- 工具：kb_search 等返回真实资产；run_scenario 默认无 runner → isError 诚实说明；
  注入 fake runner → 正常返回断言结果。
- 全程零第三方依赖（纯 json + io）。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

from tcms_ai_platform.agent.mcp_server import build_context, dispatch, serve_stdio  # noqa: E402


@NEEDS_UPSTREAM
def test_initialize_and_ping_protocol():
    ctx = build_context(UPSTREAM)
    r1 = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, ctx)
    assert r1["id"] == 1 and r1["result"]["protocolVersion"] == "2024-11-05"
    assert r1["result"]["serverInfo"]["name"] == "tcms-ai-platform-mcp"
    assert r1["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert dispatch({"id": 2, "method": "ping", "params": {}}, ctx)["result"] == {}
    # 通知不回包
    assert dispatch({"method": "notifications/initialized", "params": {}}, ctx) is None


@NEEDS_UPSTREAM
def test_tools_list_exposes_five_tools():
    ctx = build_context(UPSTREAM)
    r = dispatch({"id": 3, "method": "tools/list", "params": {}}, ctx)
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == {
        "kb_search",
        "symptom_diagnose",
        "kb_node",
        "list_scenarios",
        "kb_filter_assets",
        "run_scenario",
    }
    for t in r["result"]["tools"]:
        assert t["description"] and t["inputSchema"]


@NEEDS_UPSTREAM
def test_call_kb_search_returns_real_assets():
    ctx = build_context(UPSTREAM)
    r = dispatch({"id": 4, "method": "tools/call", "params": {"name": "kb_search", "arguments": {"query": "车门 联锁 发车"}}}, ctx)
    assert r["result"]["isError"] is False
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload.get("hits"), "kb_search 应返回真实资产命中"


@NEEDS_UPSTREAM
def test_call_run_scenario_without_runner_is_honest_error():
    ctx = build_context(UPSTREAM)
    assert ctx.runner is None
    r = dispatch(
        {"id": 5, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "overspeed_derate.yaml"}}},
        ctx,
    )
    assert r["result"]["isError"] is True
    assert "引擎" in r["result"]["content"][0]["text"]


def test_call_run_scenario_with_injected_runner():
    from types import SimpleNamespace

    calls = []

    def runner(file: str) -> dict:
        calls.append(file)
        return {"file": file, "passed": 3, "failed": 0, "all_passed": True}

    ctx = SimpleNamespace(m=None, g=None, hr=None, runner=runner)
    r = dispatch({"id": 6, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}}}, ctx)
    assert r["result"]["isError"] is False
    assert calls == ["x.yaml"]
    assert json.loads(r["result"]["content"][0]["text"])["all_passed"] is True


def test_jsonrpc_errors():
    from types import SimpleNamespace

    ctx = SimpleNamespace(m=None, g=None, hr=None, runner=None)
    # 未知方法
    e = dispatch({"id": 7, "method": "bogus", "params": {}}, ctx)
    assert e["error"]["code"] == -32601
    # 未知工具
    e2 = dispatch({"id": 8, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}, ctx)
    assert e2["error"]["code"] == -32602
    # tools/call 参数非 object
    e3 = dispatch({"id": 9, "method": "tools/call", "params": {"name": "kb_search", "arguments": "oops"}}, ctx)
    assert e3["error"]["code"] == -32602


@NEEDS_UPSTREAM
def test_stdio_end_to_end():
    ctx = build_context(UPSTREAM)
    lines = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
        'not-json',
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"symptom_diagnose","arguments":{"text":"仪表盘闪烁但无故障码"}}}',
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"run_scenario","arguments":{"scenario_file":"x"}}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}',
    ]
    out = io.StringIO()
    code = serve_stdio(ctx, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
    assert code == 0
    resp = [json.loads(x) for x in out.getvalue().strip().splitlines()]
    assert resp[0]["result"]["serverInfo"]["name"] == "tcms-ai-platform-mcp"
    names = {t["name"] for t in resp[1]["result"]["tools"]}
    assert "kb_search" in names and "run_scenario" in names and "kb_filter_assets" in names
    # 坏 JSON → -32700
    assert resp[2]["error"]["code"] == -32700
    # 诊断工具真实命中
    diag = json.loads(resp[3]["result"]["content"][0]["text"])
    assert diag["matched"] is True and diag["symptom_key"] == "dashboard_flicker"
    # run_scenario 无 runner → isError（诚实）
    assert resp[4]["result"]["isError"] is True
