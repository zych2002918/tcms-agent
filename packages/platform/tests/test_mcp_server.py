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
    http_handle,
    http_jsonrpc,
    make_runner,
    resource_catalog,
    resource_file,
    serve_stdio,
    watch_tick,
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


# ---------------------------------------------------------------------------
# 资源订阅（resources/subscribe + resources/updated）
# ---------------------------------------------------------------------------


def test_resources_subscribe_capability_is_true():
    caps = dispatch({"id": 85, "method": "initialize", "params": {}}, _bare_ctx())["result"]["capabilities"]
    assert caps["resources"]["subscribe"] is True, "已实现订阅就必须如实声明"


@NEEDS_UPSTREAM
def test_subscribe_requires_known_uri():
    ctx = build_context(UPSTREAM)
    r = dispatch({"id": 80, "method": "resources/subscribe", "params": {"uri": "tcms://fault/nope"}}, ctx)
    assert r["error"]["code"] == -32602 and "未知资源" in r["error"]["message"]
    r2 = dispatch({"id": 81, "method": "resources/unsubscribe", "params": {}}, ctx)
    assert r2["error"]["code"] == -32602 and "需要 uri" in r2["error"]["message"]


@NEEDS_UPSTREAM
def test_subscribe_function_uri_is_honestly_rejected():
    """function 是 curated 资产、无单一磁盘真源 → 诚实拒绝订阅而不是假装订阅。"""
    ctx = build_context(UPSTREAM)
    fid = sorted(ctx.m.functions)[0]
    r = dispatch({"id": 82, "method": "resources/subscribe", "params": {"uri": f"tcms://function/{fid}"}}, ctx)
    assert r["error"]["code"] == -32602 and "不支持订阅" in r["error"]["message"]
    assert not ctx.subscriptions


@NEEDS_UPSTREAM
def test_subscribe_watch_tick_and_unsubscribe():
    """订阅 → mtime 变化触发一次 → 不重复 → 退订后不再跟踪。"""
    import os

    ctx = build_context(UPSTREAM)
    scenario_file = sorted(ctx.m.scenarios)[0]
    uri = f"tcms://scenario/{scenario_file}"
    assert dispatch({"id": 83, "method": "resources/subscribe", "params": {"uri": uri}}, ctx)["result"] == {}
    assert uri in ctx.subscriptions
    assert watch_tick(ctx) == [], "未变更时不应产生通知"

    path = resource_file(ctx, uri)
    assert path is not None and path.is_file(), "场景资源必须能定位到磁盘真源"
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))  # 只改 mtime，不改内容
    assert watch_tick(ctx) == [uri]
    assert watch_tick(ctx) == [], "同一变更只通知一次"

    assert dispatch({"id": 84, "method": "resources/unsubscribe", "params": {"uri": uri}}, ctx)["result"] == {}
    assert uri not in ctx.subscriptions
    os.utime(path, (st.st_atime, st.st_mtime + 20))
    assert watch_tick(ctx) == [], "退订后不应再跟踪"


@NEEDS_UPSTREAM
def test_subscribe_fault_and_requirement_map_to_real_files():
    """fault/index → faults.yaml；requirement → rtm.csv（真源定位规则要如实）。"""
    ctx = build_context(UPSTREAM)
    fault_key = sorted(ctx.m.faults_by_key)[0]
    req_id = sorted(ctx.m.requirements)[0]
    f_fault = resource_file(ctx, f"tcms://fault/{fault_key}")
    f_index = resource_file(ctx, "tcms://index")
    f_req = resource_file(ctx, f"tcms://requirement/{req_id}")
    assert f_fault and f_fault.is_file() and f_fault.name == "faults.yaml"
    assert f_index == f_fault, "索引是派生资源，订阅它等价于订阅资产字典"
    assert f_req and f_req.is_file() and f_req.name == "rtm.csv"


# ---------------------------------------------------------------------------
# Streamable HTTP：会话管理 + SSE 流式
# ---------------------------------------------------------------------------


def _http(method, body_obj=None, headers=None, ctx=None, token=None, sessions=None, path="/mcp"):
    """驱动纯函数 HTTP 层（无需真起端口）。"""
    body = b"" if body_obj is None else json.dumps(body_obj).encode("utf-8")
    return http_handle(method, path, headers or {}, body, ctx or _bare_ctx(), token, sessions)


def test_http_session_lifecycle():
    sessions = set()
    status, headers, _ = _http("POST", {"id": 1, "method": "initialize", "params": {}}, sessions=sessions)
    assert status == 200
    sid = headers.get("Mcp-Session-Id")
    assert sid and sid in sessions, "initialize 必须下发会话 id 并登记"

    ok = _http("POST", {"id": 2, "method": "ping", "params": {}}, headers={"Mcp-Session-Id": sid}, sessions=sessions)
    assert ok[0] == 200
    bad = _http("POST", {"id": 3, "method": "ping", "params": {}}, headers={"Mcp-Session-Id": "bogus"}, sessions=sessions)
    assert bad[0] == 404 and "未知会话" in bad[2].decode("utf-8")

    assert _http("DELETE", headers={"Mcp-Session-Id": sid}, sessions=sessions)[0] == 204
    assert sid not in sessions
    assert _http("DELETE", headers={"Mcp-Session-Id": sid}, sessions=sessions)[0] == 404, "重复终止应 404"


def test_http_sse_stream_carries_progress_then_response():
    """SSE：通知帧先行、响应帧收尾 —— 流式不是装饰，进度真的在流里。"""
    ctx = _bare_ctx(runner=lambda f: {"all_passed": True, "assertions": []})
    msg = {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}, "_meta": {"progressToken": "t1"}},
    }
    status, headers, body = _http("POST", msg, headers={"Accept": "text/event-stream"}, ctx=ctx)
    assert status == 200 and headers["Content-Type"].startswith("text/event-stream")
    text = body.decode("utf-8")
    assert "event: message" in text
    objs = [json.loads(ln[len("data: "):]) for ln in text.splitlines() if ln.startswith("data: ")]
    assert [o.get("method") for o in objs[:-1]] == ["notifications/progress", "notifications/progress"]
    assert objs[-1]["id"] == 9 and objs[-1]["result"]["isError"] is False


def test_http_json_path_has_no_sse_when_not_requested():
    ctx = _bare_ctx(runner=lambda f: {"all_passed": True})
    status, headers, body = _http("POST", {"id": 4, "method": "ping", "params": {}}, ctx=ctx)
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body.decode("utf-8"))["result"] == {}


def test_http_method_and_path_errors():
    assert _http("GET")[0] == 405, "GET 拉流不支持，必须明确 405"
    assert _http("PUT")[0] == 405
    assert _http("POST", {"id": 1, "method": "ping", "params": {}}, path="/nope")[0] == 404


def test_http_auth_applies_to_sse_path_too():
    body = {"id": 5, "method": "ping", "params": {}}
    assert _http("POST", body, headers={"Accept": "text/event-stream"}, token="SECRET")[0] == 401
    assert _http("POST", body, headers={"Accept": "text/event-stream", "Authorization": "Bearer SECRET"}, token="SECRET")[0] == 200


# ---------------------------------------------------------------------------
# elicitation：人工审批（run_scenario require_approval）
# ---------------------------------------------------------------------------


def _ctx_with_requester(decision="accept", runner=None, declared=True, request=None):
    ctx = _bare_ctx(runner=runner or (lambda f: {"all_passed": True, "assertions": []}))
    ctx.client_capabilities = {"elicitation"} if declared else set()
    ctx.request = request or (
        lambda msg: {"jsonrpc": "2.0", "id": msg["id"], "result": {"action": decision, "content": {"approve": decision == "accept"}}}
    )
    return ctx


def _approval_call(rid=90):
    return {"id": rid, "method": "tools/call",
            "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml", "require_approval": True}}}


def test_approval_accept_executes():
    called = []
    ctx = _ctx_with_requester("accept", runner=lambda f: called.append(f) or {"all_passed": True})
    res = dispatch(_approval_call(), ctx)["result"]
    assert res["isError"] is False and called == ["x.yaml"]


def test_approval_decline_never_executes():
    called = []
    ctx = _ctx_with_requester("decline", runner=lambda f: called.append(f) or {"all_passed": True})
    res = dispatch(_approval_call(91), ctx)["result"]
    assert res["isError"] is True and called == [], "用户拒绝后绝不能在真实引擎上执行"
    assert "未批准" in res["content"][0]["text"]


def test_approval_without_channel_is_honest_error():
    """无反向请求通道（HTTP / dispatch-only）→ 明确报错，绝不默认放行。"""
    res = dispatch(_approval_call(92), _bare_ctx(runner=lambda f: {}))["result"]
    assert res["isError"] is True and "不支持 elicitation" in res["content"][0]["text"]


def test_approval_requires_declared_client_capability():
    ctx = _ctx_with_requester("accept", declared=False)
    res = dispatch(_approval_call(93), ctx)["result"]
    assert res["isError"] is True and "未在 initialize 声明" in res["content"][0]["text"]


def test_approval_without_flag_skips_elicitation():
    asked = []
    ctx = _ctx_with_requester("decline", request=lambda msg: asked.append(msg) or {"result": {"action": "decline"}})
    res = dispatch({"id": 94, "method": "tools/call",
                    "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}}}, ctx)["result"]
    assert res["isError"] is False and asked == [], "没要求审批就不该打扰人类"


def test_approval_malformed_response_is_error_not_pass():
    ctx = _ctx_with_requester(request=lambda msg: {"result": {"action": "maybe"}})
    res = dispatch(_approval_call(95), ctx)["result"]
    assert res["isError"] is True and "合法 action" in res["content"][0]["text"]


def test_initialize_records_client_capabilities():
    ctx = _bare_ctx()
    dispatch({"id": 1, "method": "initialize", "params": {"capabilities": {"elicitation": {}, "roots": {}}}}, ctx)
    assert ctx.client_capabilities == {"elicitation", "roots"}


# ---------------------------------------------------------------------------
# 进行中的取消语义
# ---------------------------------------------------------------------------


def test_cancel_during_execution_suppresses_response():
    """执行途中收到 cancelled → 不回包；引擎调用原子不可中断，但结果不返回。"""
    ctx = _bare_ctx()

    def runner(_f):
        ctx.cancelled.add(96)  # 模拟执行期间客户端发来 cancelled
        return {"all_passed": True}

    ctx.runner = runner
    assert dispatch({"id": 96, "method": "tools/call",
                     "params": {"name": "run_scenario", "arguments": {"scenario_file": "x.yaml"}}}, ctx) is None
