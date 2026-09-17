"""最小 MCP server（P1-b）——零新增依赖，stdio JSON-RPC。

把 TCMS 平台变成 **Model Context Protocol server**：任何 MCP client（DSH / Claude
Desktop / 自定义 harness）都能指挥它查证 TCMS 知识。

范围（诚实）：
- 协议子集：initialize / notifications/initialized / ping / tools/list / tools/call
  （+ JSON-RPC 错误码：-32700 解析、-32601 未知方法、-32602 参数、-32603 内部）。
- 工具：kb_search / symptom_diagnose / kb_node / list_scenarios（只读，来自真实
  知识底座，参数经校验、结果诚实）＋ run_scenario（需注入 engine runner；
  未接线时返回 isError=true 的诚实说明，绝不假装执行）。
- 不引入第三方库；消息 = 每行一个 JSON（UTF-8，写后 flush）。

运行：python -m tcms_ai_platform.agent.mcp_server
测试：tests/test_mcp_server.py（协议级 dispatch + 子进程 stdio smoke）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from .toolassist import TOOL_SCHEMAS, run_tool_safe

SERVER_INFO = {"name": "tcms-ai-platform-mcp", "version": "0.1.0"}
PROTOCOL = "2024-11-05"

# 额外工具：真实执行场景（默认无 runner → 诚实 error）
RUN_SCENARIO_SCHEMA = {
    "name": "run_scenario",
    "description": "在真实 TCMS 引擎上执行一个现成复现场景并返回断言结果（需要引擎已接线；否则返回错误说明）。",
    "inputSchema": {
        "type": "object",
        "properties": {"scenario_file": {"type": "string", "description": "场景文件名，如 overspeed_derate.yaml（先 list_scenarios 确认存在）"}},
        "required": ["scenario_file"],
    },
}

_READ_TOOLS: list[dict] = [
    {
        "name": s["function"]["name"],
        "description": s["function"]["description"],
        "inputSchema": s["function"]["parameters"],
    }
    for s in TOOL_SCHEMAS
]


def build_context(upstream: str | Path | None = None) -> SimpleNamespace:
    """构建 {m, g, hr, runner}；runner 默认 None（引擎未接线=诚实说明）。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    if upstream is None:
        upstream = Path(__file__).resolve().parents[4] / "tcms-can-test"
    m = load_asset_model(upstream)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    enrich_graph(g, vs)
    return SimpleNamespace(m=m, g=g, hr=HybridRetriever(vs, g), runner=None)


# ---------------------------------------------------------------------------
# JSON-RPC 处理
# ---------------------------------------------------------------------------


def _tools_list() -> list[dict]:
    return _READ_TOOLS + [dict(RUN_SCENARIO_SCHEMA)]


def _call_tool(name: str, arguments: dict, ctx: SimpleNamespace) -> dict:
    """执行工具：返回 MCP 规范的 result{content, isError}。"""
    if name == "run_scenario":
        if getattr(ctx, "runner", None) is None:
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": "run_scenario 需要真实 TCMS 引擎 runner 已接线；当前只读工具（kb_search/kb_node/symptom_diagnose/list_scenarios）全部可用。",
                    }
                ],
            }
        file = str((arguments or {}).get("scenario_file") or "").strip()
        if not file:
            return {"isError": True, "content": [{"type": "text", "text": "run_scenario 需要非空 scenario_file"}]}
        try:
            out = ctx.runner(file)
            return {"isError": False, "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
        except Exception as e:  # noqa: BLE001
            return {"isError": True, "content": [{"type": "text", "text": f"执行失败（已捕获）: {type(e).__name__}: {e}"}]}
    # 只读工具复用 toolassist 执行壳（参数校验/异常捕获一致）
    result = run_tool_safe(name, arguments if isinstance(arguments, dict) else {}, ctx.m, ctx.g, ctx.hr)
    return {"isError": bool(result.get("error")), "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


def dispatch(msg: dict, ctx: SimpleNamespace) -> dict | None:
    """处理一条 JSON-RPC 消息；返回完整应答{jsonrpc,id,result|error}；通知返回 None。"""
    method = msg.get("method")
    params = msg.get("params") or {}
    rid = msg.get("id")
    if method is None or rid is None:
        return None  # 响应/通知（无 id 不回包）
    if method == "initialize":
        protocol = str(params.get("protocolVersion") or PROTOCOL)
        return _ok(
            rid,
            {
                "protocolVersion": protocol if protocol.startswith("202") else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": dict(SERVER_INFO),
            },
        )
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": _tools_list()})
    if method == "tools/call":
        name = str((params or {}).get("name") or "")
        arguments = (params or {}).get("arguments") or {}
        if not name or not isinstance(arguments, dict):
            return _err(rid, -32602, "tools/call 需要 {name, arguments(object)}")
        known = {t["name"] for t in _tools_list()}
        if name not in known:
            return _err(rid, -32602, f"未知工具: {name}")
        return _ok(rid, _call_tool(name, arguments, ctx))
    return _err(rid, -32601, f"未知方法: {method}")


def _ok(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# stdio 服务
# ---------------------------------------------------------------------------


def serve_stdio(ctx: SimpleNamespace, stdin=None, stdout=None) -> int:
    """逐行读取 stdin JSON-RPC 并应答；返回退出码（Ctrl-D/EOF → 0）。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        resp = dispatch(msg, ctx) if isinstance(msg, dict) else _err(None, -32700, "parse error")
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main() -> None:
    ctx = build_context()
    raise SystemExit(serve_stdio(ctx))


if __name__ == "__main__":
    main()
