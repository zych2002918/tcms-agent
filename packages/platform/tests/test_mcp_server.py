"""MCP server 门禁（Slice 1/2/3）：协议协商 + 工具 + 资源 + 提示 + 进度 + 取消 + 两种传输。

- dispatch 覆盖 initialize/tools/resources/prompts/取消/未知方法/坏 JSON(-32700)。
- 工具：kb_search 等返回真实资产；run_scenario 默认已接真实引擎（Slice 1）；失败四类各自成文。
- 资源：分页不静默截断（nextCursor）、read 覆盖 5 类 URI、未知 URI 报错。
- 提示：4 个模板；缺必填参数报错。
- 进度：带 progressToken 时发 notifications/progress。
- HTTP：纯函数 http_jsonrpc（鉴权/坏 JSON/通知 202）。
- 全程零第三方依赖（纯 json + io + http.server）。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "engine"  # monorepo: packages/engine
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)

from tcms_ai_platform.agent.mcp_server import (  # noqa: E402
    PROMPTS,
    PROTOCOL,
    RESOURCE_PAGE,
    SUPPORTED_PROTOCOLS,
    build_context,
    dispatch,
    http_jsonrpc,
    make_runner,
    resource_catalog,
    serve_stdio,
)


def _bare_ctx(**kw):
    """不加载资产的最小上下文（用于协议/进度/取消/HTTP 的纯逻辑测试）。"""
    base = dict(m=None, g=None, hr=None, runner=None, notify=None, cancelled=set())
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 协议：初始化 / 协商 / 能力
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_initialize_and_ping_protocol():
    ctx = build_context(UPSTREAM)
    r1 = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, ctx)
    assert r1["id"] == 1 and r1["result"]["protocolVersion"] == "2024-11-05"
    assert r1["result"]["serverInfo"]["name"] == "tcms-ai-platform-mcp"
    assert dispatch({"id": 2, "method": "ping", "params": {}}, ctx)["result"] == {}
    assert dispatch({"method": "notifications/initialized", "params": {}}, ctx) is None


def test_initialize_negotiates_unsupported_version():
    """请求不支持的版本 → 回本实现支持的版本（而不是原样回显）。"""
    ctx = _bare_ctx()
    r = dispatch({"id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}}, ctx)
    assert r["result"]["protocolVersion"] == PROTOCOL
    assert PROTOCOL in SUPPORTED_PROTOCOLS


def test_capabilities_declare_three_primitives():
    ctx = _bare_ctx()
    caps = dispatch({"id": 1, "method": "initialize", "params": {}}, ctx)["result"]["capabilities"]
    assert set(caps) == {"tools", "resources", "prompts"}


@NEEDS_UPSTREAM
def test_tools_list_exposes_tools():
    """6 个工具（只读 5 + R2 真实执行 1），每个都带 annotations。"""
    ctx = build_context(UPSTREAM)
    tools = dispatch({"id": 3, "method": "tools/list", "params": {}}, ctx)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"kb_search", "kb_filter_assets", "symptom_diagnose", "kb_node", "list_scenarios", "run_scenario"}
    assert len(tools) == 6
    for t in tools:
        assert t["description"] and t["inputSchema"] and "annotations" in t
    read_only = {t["name"] for t in tools if t["annotations"]["readOnlyHint"]}
    assert read_only == names - {"run_scenario"}


@NEEDS_UPSTREAM
def test_call_kb_search_returns_real_assets():
    ctx = build_context(UPSTREAM)
    r = dispatch({"id": 4, "method": "tools/call", "params": {"name": "kb_search", "arguments": {"query": "车门 联锁 发车"}}}, ctx)
    assert r["result"]["isError"] is False
    assert json.loads(r["result"]["content"][0]["text"]).get("hits")


# ---------------------------------------------------------------------------
# 资源：分页 / 读取 / 未知
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_resources_list_paginates_without_silent_truncation():
    ctx = build_context(UPSTREAM)
    total = len(resource_catalog(ctx.m))
    assert total > RESOURCE_PAGE, "资产量应超过一页，才能验证分页"
    seen, cursor, pages = [], None, 0
    while True:
        params = {"cursor": cursor} if cursor else {}
        res = dispatch({"id": 10, "method": "resources/list", "params": params}, ctx)["result"]
        seen += [x["uri"] for x in res["resources"]]
        pages += 1
        cursor = res.get("nextCursor")
        if not cursor:
            break
        assert pages < 100, "分页未收敛"
    assert len(seen) == total and len(set(seen)) == total
    assert seen[0] == "tcms://index"


@NEEDS_UPSTREAM
def test_resources_read_covers_every_uri_kind():
    ctx = build_context(UPSTREAM)
    fault_key = sorted(ctx.m.faults_by_key)[0]
    scenario_file = sorted(ctx.m.scenarios)[0]
    req_id = sorted(ctx.m.requirements)[0]
    fid = sorted(ctx.m.functions)[0]
    for uri in ("tcms://index", f"tcms://fault/{fault_key}", f"tcms://scenario/{scenario_file}",
                f"tcms://requirement/{req_id}", f"tcms://function/{fid}"):
        res = dispatch({"id": 11, "method": "resources/read", "params": {"uri": uri}}, ctx)["result"]
        content = res["contents"][0]
        assert content["uri"] == uri and content["mimeType"] == "application/json"
        assert json.loads(content["text"]), f"{uri} 内容不应为空"


@NEEDS_UPSTREAM
def test_resources_read_unknown_uri_is_error():
    ctx = build_context(UPSTREAM)
    r = dispatch({"id": 12, "method": "resources/read", "params": {"uri": "tcms://fault/not_a_fault"}}, ctx)
    assert r["error"]["code"] == -32602 and "未知资源" in r["error"]["message"]
    r2 = dispatch({"id": 13, "method": "resources/read", "params": {}}, ctx)
    assert r2["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# 提示
# ---------------------------------------------------------------------------


def test_prompts_list_and_get():
    ctx = _bare_ctx()
    listed = dispatch({"id": 20, "method": "prompts/list", "params": {}}, ctx)["result"]["prompts"]
    assert {p["name"] for p in listed} == set(PROMPTS)
    for p in listed:
        assert p["description"] and "arguments" in p

    got = dispatch({"id": 21, "method": "prompts/get", "params": {"name": "diagnose_symptom", "arguments": {"symptom_text": "仪表盘闪烁但无故障码"}}}, ctx)["result"]
    text = got["messages"][0]["content"]["text"]
    assert "仪表盘闪烁但无故障码" in text and "symptom_diagnose" in text


def test_prompts_get_validation_errors():
    ctx = _bare_ctx()
    missing = dispatch({"id": 22, "method": "prompts/get", "params": {"name": "case_from_fault", "arguments": {}}}, ctx)
    assert missing["error"]["code"] == -32602 and "需要参数" in missing["error"]["message"]
    unknown = dispatch({"id": 23, "method": "prompts/get", "params": {"name": "nope", "arguments": {}}}, ctx)
    assert unknown["error"]["code"] == -32602 and "未知 prompt" in unknown["error"]["message"]


# ---------------------------------------------------------------------------
# R2 真实执行 + 四类失败 + 进度 + 取消
# ---------------------------------------------------------------------------


@NEEDS_UPSTREAM
def test_run_scenario_executes_real_engine_by_default():
    ctx = build_context(UPSTREAM)
    assert ctx.runner is not None, "Slice 1 要求 build_context 默认接线 runner"
    if not ctx.m.scenarios:
        pytest.skip("资产模型没有场景")
    try:
        import tcms.scenarios  # noqa: F401
    except ImportError:
        pytest.skip("tcms 引擎不可导入（未安装且无活上游）")
    name = next(iter(ctx.m.scenarios))
    res = dispatch({"id": 50, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": name}}}, ctx)["result"]
    assert res["isError"] is False, res["content"][0]["text"][:300]
    payload = json.loads(res["content"][0]["text"])
    assert payload["scenario_file"] == name and payload["assertions"] is not None
    assert payload["engine_version"]


@NEEDS_UPSTREAM
def test_run_scenario_unknown_name_is_scenario_not_found():
    ctx = build_context(UPSTREAM)
    res = dispatch({"id": 51, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "no_such_scenario_xyz.yaml"}}}, ctx)["result"]
    assert res["isError"] is True
    txt = res["content"][0]["text"]
    assert "场景不存在" in txt and "引擎不可用" not in txt


def test_run_scenario_file_missing_on_disk_is_distinct(tmp_path):
    """模型里有、磁盘上没有 → FileNotFoundError（该分支排在 import tcms 之前）。"""

    class FakeModel:
        def scenario(self, file):
            if file != "ghost.yaml":
                raise KeyError(file)
            return object()

    with pytest.raises(FileNotFoundError):
        make_runner(FakeModel(), tmp_path)("ghost.yaml")


def test_run_scenario_engine_unavailable_gives_guidance(tmp_path, monkeypatch):
    (tmp_path / "ok.yaml").write_text("name: t\nsteps: []\n", encoding="utf-8")

    class FakeModel:
        def scenario(self, file):
            return object()

    runner = make_runner(FakeModel(), tmp_path)
    monkeypatch.setitem(sys.modules, "tcms.scenarios", None)
    res = dispatch({"id": 60, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "ok.yaml"}}},
                   _bare_ctx(runner=runner))["result"]
    assert res["isError"] is True
    txt = res["content"][0]["text"]
    assert "引擎不可用" in txt and "tcms-can-test" in txt and "场景不存在" not in txt


def test_run_scenario_without_runner_is_honest_error():
    res = dispatch({"id": 61, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}}},
                   _bare_ctx())["result"]
    assert res["isError"] is True and "引擎" in res["content"][0]["text"]


def test_run_scenario_requires_non_empty_file():
    res = dispatch({"id": 62, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "  "}}},
                   _bare_ctx(runner=lambda f: {}))["result"]
    assert res["isError"] is True and "非空" in res["content"][0]["text"]


def test_progress_notifications_when_token_present():
    sent = []
    ctx = _bare_ctx(runner=lambda f: {"all_passed": True, "assertions": []}, notify=sent.append)
    res = dispatch({
        "id": 70, "method": "tools/call",
        "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}, "_meta": {"progressToken": "tok-1"}},
    }, ctx)["result"]
    assert res["isError"] is False
    assert [n["method"] for n in sent] == ["notifications/progress", "notifications/progress"]
    assert all(n["params"]["progressToken"] == "tok-1" for n in sent)
    assert sent[0]["params"]["progress"] < sent[1]["params"]["progress"]


def test_no_progress_notifications_without_token():
    sent = []
    ctx = _bare_ctx(runner=lambda f: {"all_passed": True}, notify=sent.append)
    dispatch({"id": 71, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}}}, ctx)
    assert sent == []


def test_cancelled_request_gets_no_response():
    ctx = _bare_ctx(runner=lambda f: {"all_passed": True})
    assert dispatch({"method": "notifications/cancelled", "params": {"requestId": 99}}, ctx) is None
    assert dispatch({"id": 99, "method": "tools/call", "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}}}, ctx) is None
    # 其它 id 不受影响
    assert dispatch({"id": 100, "method": "ping", "params": {}}, ctx)["result"] == {}


# ---------------------------------------------------------------------------
# HTTP 传输（纯函数）
# ---------------------------------------------------------------------------


def _body(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8")


def test_http_jsonrpc_happy_path_and_notification():
    ctx = _bare_ctx()
    status, payload = http_jsonrpc(_body({"id": 1, "method": "ping", "params": {}}), ctx)
    assert status == 200 and payload["result"] == {}
    status, payload = http_jsonrpc(_body({"method": "notifications/initialized", "params": {}}), ctx)
    assert status == 202 and payload is None


def test_http_jsonrpc_auth():
    ctx = _bare_ctx()
    body = _body({"id": 1, "method": "ping", "params": {}})
    assert http_jsonrpc(body, ctx, None, "SECRET")[0] == 401
    assert http_jsonrpc(body, ctx, "Bearer WRONG", "SECRET")[0] == 401
    assert http_jsonrpc(body, ctx, "Bearer SECRET", "SECRET")[0] == 200
    assert http_jsonrpc(body, ctx, None, None)[0] == 200, "未配置 token 时不应鉴权"


def test_http_jsonrpc_bad_input():
    ctx = _bare_ctx()
    status, payload = http_jsonrpc(b"not-json", ctx)
    assert status == 400 and payload["error"]["code"] == -32700
    status, payload = http_jsonrpc(b"[1,2]", ctx)
    assert status == 400 and payload["error"]["code"] == -32600


# ---------------------------------------------------------------------------
# JSON-RPC 错误码 / stdio 端到端
# ---------------------------------------------------------------------------


def test_jsonrpc_errors():
    ctx = _bare_ctx()
    assert dispatch({"id": 7, "method": "bogus", "params": {}}, ctx)["error"]["code"] == -32601
    assert dispatch({"id": 8, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}, ctx)["error"]["code"] == -32602
    assert dispatch({"id": 9, "method": "tools/call", "params": {"name": "kb_search", "arguments": "oops"}}, ctx)["error"]["code"] == -32602


@NEEDS_UPSTREAM
def test_stdio_end_to_end():
    ctx = build_context(UPSTREAM)
    lines = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
        'not-json',
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"symptom_diagnose","arguments":{"text":"仪表盘闪烁但无故障码"}}}',
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"run_scenario","arguments":{"scenario_file":"x"}}}',
        '{"jsonrpc":"2.0","id":5,"method":"resources/list","params":{}}',
        '{"jsonrpc":"2.0","id":6,"method":"prompts/list","params":{}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}',
    ]
    out = io.StringIO()
    assert serve_stdio(ctx, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out) == 0
    resp = [json.loads(x) for x in out.getvalue().strip().splitlines()]
    assert resp[0]["result"]["serverInfo"]["name"] == "tcms-ai-platform-mcp"
    assert set(resp[0]["result"]["capabilities"]) == {"tools", "resources", "prompts"}
    names = {t["name"] for t in resp[1]["result"]["tools"]}
    assert len(names) == 6 and "kb_search" in names and "run_scenario" in names
    assert resp[2]["error"]["code"] == -32700
    diag = json.loads(resp[3]["result"]["content"][0]["text"])
    assert diag["matched"] is True and diag["symptom_key"] == "dashboard_flicker"
    assert resp[4]["result"]["isError"] is True and "场景不存在" in resp[4]["result"]["content"][0]["text"]
    assert resp[5]["result"]["resources"] and resp[5]["result"].get("nextCursor")
    assert {p["name"] for p in resp[6]["result"]["prompts"]} == set(PROMPTS)
