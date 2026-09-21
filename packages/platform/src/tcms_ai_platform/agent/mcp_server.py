"""MCP server（P1-b → Slice 1/2/3）——零第三方依赖，stdio + 最小 HTTP。

把 TCMS 平台变成 **Model Context Protocol server**：任何 MCP client（DSH / Claude
Desktop / 自定义 harness）都能指挥它查证 TCMS 知识、**真实执行**复现场景，并按规范
消费 resources / prompts 两个原语。

实现范围（诚实）：
- **协议**：`initialize`（**真协商**：不支持的版本回自己支持的；见 `SUPPORTED_PROTOCOLS`）·
  `notifications/initialized` · `ping` · `tools/list` · `tools/call` ·
  **`resources/list`（带 `nextCursor` 分页）** · **`resources/read`** ·
  **`prompts/list`** · **`prompts/get`** · `notifications/cancelled`（协作式）。
- **能力声明**：`tools` / `resources` / `prompts` 三项如实声明（不再只声明 tools）。
- **工具 6 个**：kb_search / kb_filter_assets / symptom_diagnose / kb_node /
  list_scenarios（只读）＋ run_scenario（**R2 真实执行**，默认接真实引擎）。
- **进度**：`tools/call` 带 `_meta.progressToken` 时，`run_scenario` 会发
  `notifications/progress`。
- **取消**：收到 `notifications/cancelled` 后，该请求不再回包（规范：MUST NOT respond）；
  ⚠️ 诚实边界——引擎执行本身不可中断，取消只对**尚未开始**的请求生效（见 docs）。
- **传输**：stdio（逐行 JSON-RPC）+ **Streamable HTTP**：`POST /mcp` 返回 JSON，
  或带 `Accept: text/event-stream` 时返回 **SSE 流**（进度等通知逐帧先行、响应帧收尾）；
  `initialize` 下发 **`Mcp-Session-Id`**（后续请求带上，未知会话 404，`DELETE /mcp` 终止）；
  可选 `Authorization: Bearer <token>`。⚠️ 未做 OAuth 与断线重放（单机自托管定位）。
- **资源订阅**：`resources/subscribe` / `resources/unsubscribe`；底层文件 mtime 变化时发
  `notifications/resources/updated`（stdio/HTTP 模式下由守护线程驱动，纯函数 `watch_tick` 可单测）。
- **elicitation（人工审批）**：`run_scenario` 带 `require_approval=true` 时，服务端用
  `elicitation/create` **反向请求客户端**由人类批准；未声明能力的客户端/不支持的传输 → 明确报错，
  用户拒绝 → **绝不执行**（与 ADR-007「审批放独立节点」同源）。
- 仍未实现：`Tasks` 扩展（异步长任务）—— 见 `docs/MCP_HARDENING.md` Slice 4。
- 不引入第三方库；stdio 消息 = 每行一个 JSON（UTF-8，写后 flush）。

运行：python -m tcms_ai_platform.agent.mcp_server            # stdio（或 `tcms-mcp`）
      python -m tcms_ai_platform.agent.mcp_server --http --port 8765 --token SECRET
测试：tests/test_mcp_server.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from .toolassist import TOOL_SCHEMAS, run_tool_safe

SERVER_INFO = {"name": "tcms-ai-platform-mcp", "version": "0.6.0"}

#: 本实现自身支持的协议版本（协商用；不支持请求版本时回第一个 = 最新支持版本）
SUPPORTED_PROTOCOLS = ("2026-07-28", "2025-06-18", "2024-11-05")
PROTOCOL = SUPPORTED_PROTOCOLS[0]

#: resources/list 单页条数（超出给 nextCursor —— 绝不静默截断）
RESOURCE_PAGE = 50

#: 引擎缺失时的可操作引导（与 server/app.py 的 /api/run/scenario 文案保持一致）
ENGINE_HINT = (
    "TCMS 引擎不可用：场景执行需要 tcms-can-test。"
    "请 pip install tcms-can-test，或设置 TCMS_UPSTREAM_DIR 指向其目录。"
)


class EngineUnavailable(RuntimeError):
    """引擎不可用（未安装 tcms 且无活上游）——由 runner 抛出，供上层给引导文案。"""


RUN_SCENARIO_SCHEMA = {
    "name": "run_scenario",
    "description": (
        "在真实 TCMS 引擎上执行一个现成复现场景并返回断言结果（R2 真实执行层）。"
        "先用 list_scenarios 确认场景名；失败时会明确区分『场景不存在』与『引擎不可用』。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "scenario_file": {"type": "string", "description": "场景文件名，如 overspeed_derate.yaml（先 list_scenarios 确认存在）"},
            "require_approval": {
                "type": "boolean",
                "description": "true=执行前先用 MCP elicitation 向人类请求批准（有副作用的真实执行建议开启）；"
                "客户端未声明 elicitation 能力或传输不支持时不会假装问过，而是明确报错。",
            },
        },
        "required": ["scenario_file"],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}

_READ_TOOLS: list[dict] = [
    {
        "name": s["function"]["name"],
        "description": s["function"]["description"],
        "inputSchema": s["function"]["parameters"],
    }
    for s in TOOL_SCHEMAS
]
for _t in _READ_TOOLS:
    _t["annotations"] = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}


# ---------------------------------------------------------------------------
# 上下文构建
# ---------------------------------------------------------------------------


def make_runner(model, scenario_dir: str | Path):
    """构造 `scenario_file -> 报告` 的 runner（与 `server/app.py::/api/run/scenario` 同构）。

    检查顺序有讲究（决定错误文案是否诚实）：
    ① 模型里有这个名字吗（无 → KeyError）② 磁盘上有这个 YAML 吗（无 → FileNotFoundError）
    ③ 引擎能 import 吗（不能 → EngineUnavailable）④ 执行（异常原样抛）。

    先查磁盘再查引擎：文件都不存在时说"引擎不可用"是误导。
    """
    scen_dir = Path(scenario_dir)

    def runner(scenario_file: str) -> dict:
        file = str(scenario_file or "").strip()
        model.scenario(file)  # ① 校验存在（不存在抛 KeyError）
        path = scen_dir / file
        if not path.is_file():  # ② 模型有、磁盘无（如上游目录变动 / 只装了快照）
            raise FileNotFoundError(str(path))
        try:  # ③ 引擎
            import tcms.scenarios as sc  # noqa: PLC0415
        except ImportError as e:
            raise EngineUnavailable(f"{ENGINE_HINT}({e})") from None
        rep = sc.run_yaml(str(path))  # ④ 真实执行（与 REST 端点同一条引擎路径）
        out = {k: rep.get(k) for k in ("scenario", "steps", "assertions", "passed", "failed", "all_passed")}
        out["scenario_file"] = file
        out["engine_version"] = __import__("tcms").__version__
        return out

    return runner


def build_context(upstream: str | Path | None = None, with_runner: bool = True, notify=None) -> SimpleNamespace:
    """构建 {m, g, hr, runner, scenario_dir, notify, cancelled}。

    - `upstream=None`：走平台自己的**四级资产解析链**（用户设置 → 环境变量 → 同仓成员
      packages/engine → 内置快照），并 `ensure_engine_importable` 把活上游入 sys.path。
    - `upstream=<path>`：直接按该根加载（`<root>/scenarios`），并把该根入 sys.path。
    - `with_runner=False`：显式构造"未接线"上下文（只读部署 / 诚实错误路径测试）。
    - `notify`：JSON-RPC 通知写出器（stdio/HTTP 各自注入；为 None 则不发进度）。

    `upstream=None` 时走平台解析链 —— 这里曾经手拼过指向旧"双仓平级 clone"布局的
    兄弟目录路径，三仓合一后它指向不存在的目录，导致 **pip 安装的用户一启动就崩**。
    教训：**能用平台的解析链就不要自己拼路径**；自己拼的路径不会随布局演进而更新。
    """
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.core.loader import load_from_source
    from tcms_ai_platform.core.sources import (
        bundled_scenarios_fallback,
        ensure_engine_importable,
        resolve_asset_source,
    )
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    scenario_dir: Path
    if upstream is None:
        source = resolve_asset_source()
        m = load_from_source(source)  # 活上游 / 内置快照都能处理
        ensure_engine_importable(source)  # 活上游入 sys.path；否则尝试已安装 tcms
        scenario_dir = Path(source.scenarios_dir)
    else:
        root = Path(upstream)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        m = load_asset_model(root)
        candidate = root / "scenarios"
        scenario_dir = candidate if candidate.is_dir() else (bundled_scenarios_fallback() or candidate)

    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    enrich_graph(g, vs)
    runner = make_runner(m, scenario_dir) if with_runner else None
    return SimpleNamespace(
        m=m, g=g, hr=HybridRetriever(vs, g), runner=runner, scenario_dir=scenario_dir,
        notify=notify, cancelled=set(), subscriptions={}, request=None, client_capabilities=set(),
    )


# ---------------------------------------------------------------------------
# 资源（resources）—— 全部来自真实资产，URI 可寻址
# ---------------------------------------------------------------------------


def resource_catalog(m) -> list[dict]:
    """把真实资产展开为资源目录（顺序稳定：index → faults → scenarios → requirements → functions）。"""
    out = [
        {
            "uri": "tcms://index",
            "name": "TCMS 资产索引",
            "description": "资产计数与各集合的 URI 前缀（fault/scenario/requirement/function）",
            "mimeType": "application/json",
        }
    ]
    for key in sorted(m.faults_by_key):
        f = m.faults_by_key[key]
        out.append({
            "uri": f"tcms://fault/{key}",
            "name": f"{f.name}（{f.subsystem}）",
            "description": f"FMEA：等级 {f.level} / 处置 {f.action} / SIL {f.sil}",
            "mimeType": "application/json",
        })
    for file in sorted(m.scenarios):
        s = m.scenarios[file]
        out.append({
            "uri": f"tcms://scenario/{file}",
            "name": s.desc or s.name,
            "description": f"可执行复现场景（{len(s.steps)} 步 / 故障 {len(s.fault_keys)} 个）",
            "mimeType": "application/json",
        })
    for req_id in sorted(m.requirements):
        # 一个 req_id 可能对应多行 RTM 映射（module/test_file）——资源 URI 必须唯一，
        # 因此这里**每个 req_id 只登记一条**，多行内容由 resources/read 一并返回。
        rows = m.requirements[req_id]
        head = rows[0] if rows else None
        out.append({
            "uri": f"tcms://requirement/{req_id}",
            "name": f"{req_id} → {head.module}" if head else req_id,
            "description": (head.verifies if head else "") + (f"（共 {len(rows)} 条实现/测试映射）" if len(rows) > 1 else ""),
            "mimeType": "application/json",
        })
    for fid in sorted(m.functions):
        fn = m.functions[fid]
        out.append({
            "uri": f"tcms://function/{fid}",
            "name": fn.name,
            "description": fn.description,
            "mimeType": "application/json",
        })
    return out


def read_resource(m, uri: str) -> tuple[str, str]:
    """读取资源 → (mimeType, text)；未知 URI 抛 KeyError。"""
    uri = str(uri or "").strip()
    if uri == "tcms://index":
        return "application/json", json.dumps(
            {
                "stats": m.stats(),
                "uri_prefixes": {
                    "fault": "tcms://fault/{key}",
                    "scenario": "tcms://scenario/{file}",
                    "requirement": "tcms://requirement/{req_id}",
                    "function": "tcms://function/{fid}",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    if not uri.startswith("tcms://"):
        raise KeyError(uri)
    kind, _, ident = uri[len("tcms://"):].partition("/")
    if kind == "fault":
        f = m.faults_by_key[ident]
        return "application/json", json.dumps(
            {
                "fid": f.fid, "key": f.key, "name": f.name, "subsystem": f.subsystem,
                "layer": f.layer, "level": f.level, "action": f.action, "sil": f.sil,
                "desc": f.desc, "detect": f.detect, "inject": f.inject,
                "recovery": f.recovery, "action_note": f.action_note,
            },
            ensure_ascii=False, indent=2,
        )
    if kind == "scenario":
        s = m.scenarios[ident]
        return "application/json", json.dumps(
            {
                "file": s.file, "name": s.name, "desc": s.desc,
                "fault_keys": sorted(s.fault_keys), "nodes": sorted(s.nodes),
                "steps": [
                    {"at": st.at, "action": st.action, "node": st.node, "fault": st.fault,
                     "level": st.level, "expect": st.expect, "impact": st.impact}
                    for st in s.steps
                ],
            },
            ensure_ascii=False, indent=2,
        )
    if kind == "requirement":
        rs = m.requirements[ident]
        return "application/json", json.dumps(
            [{"req_id": r.req_id, "module": r.module, "test_file": r.test_file,
              "verifies": r.verifies, "status": r.status} for r in rs],
            ensure_ascii=False, indent=2,
        )
    if kind == "function":
        fn = m.functions[ident]
        return "application/json", json.dumps(
            {"fid": fn.fid, "name": fn.name, "description": fn.description,
             "messages": list(fn.messages), "signals": list(fn.signals),
             "fault_keys": list(fn.fault_keys), "requirements": list(fn.requirements)},
            ensure_ascii=False, indent=2,
        )
    raise KeyError(uri)


# ---------------------------------------------------------------------------
# 资源订阅（resources/subscribe）—— 底层文件 mtime 变化 → resources/updated
# ---------------------------------------------------------------------------


def resource_file(ctx: SimpleNamespace, uri: str) -> Path | None:
    """返回该资源对应的**磁盘真源**文件；无法定位则 None（→ 诚实拒绝订阅）。

    - `tcms://scenario/{file}` → 场景 YAML 本体
    - `tcms://fault/{key}` / `tcms://index` → `<upstream>/tcms/faults.yaml`
    - `tcms://requirement/{req_id}` → `<upstream>/tests/rtm.csv`
    - `tcms://function/{fid}` → None（curated 资产，无单一磁盘真源）
    """
    uri = str(uri or "").strip()
    if not uri.startswith("tcms://"):
        return None
    kind, _, ident = uri[len("tcms://"):].partition("/")
    root = Path(str(getattr(ctx.m, "source_upstream", "") or ""))
    if kind == "scenario":
        p = Path(str(getattr(ctx, "scenario_dir", ""))) / ident
    elif kind in ("fault", "index"):
        p = root / "tcms" / "faults.yaml"
    elif kind == "requirement":
        p = root / "tests" / "rtm.csv"
    else:
        return None  # function 等 curated 资产：无单一磁盘真源
    return p if p.is_file() else None


def set_subscription(ctx: SimpleNamespace, uri: str, on: bool):
    """订阅/退订；返回 (result, error_message)。校验 URI 存在且可定位底层文件。"""
    known = {r["uri"] for r in resource_catalog(ctx.m)}
    if uri not in known:
        return None, f"未知资源: {uri}（先用 resources/list 取可用 URI）"
    path = resource_file(ctx, uri)
    if path is None:
        return None, f"该资源不支持订阅（底层文件不可定位）: {uri}"
    subs = getattr(ctx, "subscriptions", None)
    if subs is None:
        subs = ctx.subscriptions = {}
    if on:
        subs[uri] = path.stat().st_mtime
    else:
        subs.pop(uri, None)
    return {}, None


def watch_tick(ctx: SimpleNamespace) -> list[str]:
    """比对已订阅资源的 mtime，返回发生变化的 uri 列表（纯函数，便于单测）。"""
    subs = getattr(ctx, "subscriptions", None) or {}
    changed: list[str] = []
    for uri, seen in list(subs.items()):
        path = resource_file(ctx, uri)
        if path is None:
            continue
        mtime = path.stat().st_mtime
        if mtime != seen:
            subs[uri] = mtime
            changed.append(uri)
    return changed


def start_watcher(ctx: SimpleNamespace, interval: float = 2.0):
    """守护线程：定期 watch_tick 并在变更时发 notifications/resources/updated。"""
    import threading
    import time as _time

    def loop() -> None:
        while True:
            _time.sleep(interval)
            try:
                for uri in watch_tick(ctx):
                    _emit(ctx, "notifications/resources/updated", {"uri": uri})
            except Exception:  # noqa: BLE001 - 守护线程绝不该把进程带崩
                pass

    t = threading.Thread(target=loop, daemon=True, name="mcp-resource-watch")
    t.start()
    return t


# ---------------------------------------------------------------------------
# 提示（prompts）—— 参数化任务模板，全部只用本 server 真实暴露的工具
# ---------------------------------------------------------------------------

PROMPTS: dict[str, dict] = {
    "diagnose_symptom": {
        "description": "无码症状 → 候选故障链与验证动作（用 symptom_diagnose + kb_search + run_scenario）",
        "arguments": [{"name": "symptom_text", "description": "现象描述，如『仪表盘闪烁但无故障码』", "required": True}],
    },
    "case_from_fault": {
        "description": "给定故障键，产出可执行的测试思路与待验证处置",
        "arguments": [{"name": "fault_key", "description": "真实故障键（kb_filter_assets 可枚举）", "required": True}],
    },
    "verify_fault_action": {
        "description": "验证『某故障是否真的触发某处置』：挑覆盖场景并真实执行",
        "arguments": [
            {"name": "fault_key", "description": "真实故障键", "required": True},
            {"name": "action", "description": "期望处置（none/warning/derate/emergency_brake/shutdown）", "required": False},
        ],
    },
    "scenario_report": {
        "description": "执行一个复现场景并汇总断言结果",
        "arguments": [{"name": "scenario_file", "description": "场景文件名（list_scenarios 可枚举）", "required": True}],
    },
}

_TOOL_GUIDE = (
    "可用工具（本 server 真实暴露，共 6 个）：kb_search（混合检索）、kb_filter_assets（枚举故障/场景）、"
    "symptom_diagnose（无码症状多跳诊断）、kb_node（图谱节点）、list_scenarios（列出可执行场景）、"
    "run_scenario（在真实引擎上执行场景，R2 真实执行层）。"
    "资源可用 tcms://index、tcms://fault/{key}、tcms://scenario/{file}、"
    "tcms://requirement/{req_id}、tcms://function/{fid} 寻址。"
)


def get_prompt(name: str, arguments: dict) -> dict:
    """构造 prompts/get 结果；缺必填参数抛 ValueError。"""
    spec = PROMPTS.get(name)
    if spec is None:
        raise KeyError(name)
    args = {k: str(v).strip() for k, v in (arguments or {}).items() if v is not None}
    for a in spec["arguments"]:
        if a.get("required") and not args.get(a["name"]):
            raise ValueError(f"prompt {name} 需要参数 {a['name']}")

    if name == "diagnose_symptom":
        text = (
            f"现象：{args['symptom_text']}\n\n"
            "请按此顺序作业：① 用 symptom_diagnose 做确定性多跳诊断；② 对候选故障用 kb_filter_assets/kb_node 取证据；"
            "③ 用 list_scenarios 找覆盖这些故障的复现场景；④ 需要确认系统真实动作时用 run_scenario 执行场景，"
            "并逐条引用引擎返回的 assertions。不确定就明说 no_match，不要编造故障码。\n\n" + _TOOL_GUIDE
        )
    elif name == "case_from_fault":
        text = (
            f"目标故障：{args['fault_key']}\n\n"
            "请：① 读资源 tcms://fault/{key} 拿到 FMEA 语义（等级/处置/注入方式/恢复条件）；"
            "② 用 kb_filter_assets 找同域相关故障与场景；③ 产出针对该故障的测试思路（前置条件、注入、观测点、通过判据），"
            "并标注哪些结论有资产出处、哪些是推断。\n\n" + _TOOL_GUIDE
        )
    elif name == "verify_fault_action":
        want = args.get("action") or "（未指定，取 FMEA 默认处置）"
        text = (
            f"待验证：故障 {args['fault_key']} 是否触发处置 {want}。\n\n"
            "请：① 读 tcms://fault/{fault_key} 取 expect 语义；② 用 list_scenarios(fault_key=...) 或 kb_filter_assets 找覆盖场景；"
            "③ 用 run_scenario 真实执行，依据 assertions 给出 verified 与否；④ 若没有覆盖场景，明确说『无覆盖场景』而不是推断。\n\n" + _TOOL_GUIDE
        )
    else:  # scenario_report
        text = (
            f"请执行场景 {args['scenario_file']}：① 先读 tcms://scenario/{args['scenario_file']} 了解步骤与涉及故障；"
            "② 用 run_scenario 执行；③ 汇总 passed/failed/all_passed 与每条断言，失败时给出定位线索。\n\n" + _TOOL_GUIDE
        )
    return {
        "description": spec["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": _fix_uri_placeholders(text)}}],
    }


def _fix_uri_placeholders(text: str) -> str:
    """把模板里的 `{key}` / `{file}` 占位换成可读写法（避免与 .format 语义混淆）。"""
    return text.replace("{fault_key}", "该故障键").replace("{key}", "该故障键").replace("{file}", "该场景文件")


# ---------------------------------------------------------------------------
# JSON-RPC 处理
# ---------------------------------------------------------------------------


def _text_ok(payload: dict) -> dict:
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _text_err(message: str) -> dict:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def request_approval(ctx: SimpleNamespace, scenario_file: str):
    """通过 MCP elicitation 向人类请求执行审批 → (decision, error_message)。

    decision ∈ {accept, decline, cancel}；任何"问不到人"的情况都返回 error，
    **绝不默认放行**（审批不是装饰）。
    """
    requester = getattr(ctx, "request", None)
    if not callable(requester):
        return None, (
            "当前传输不支持 elicitation（服务端→客户端反向请求）：stdio 的 `tcms-mcp` 支持；"
            "HTTP 模式暂不支持。若确要走无审批路径，请显式传 require_approval=false —— "
            "但那是你自己的审批纪律决定，不由我替你默认。"
        )
    declared = set(getattr(ctx, "client_capabilities", None) or set())
    if "elicitation" not in declared:
        return None, "客户端未在 initialize 声明 elicitation 能力，无法发起审批（不假装问过）。"
    msg = {
        "jsonrpc": "2.0",
        "id": f"elicit-{uuid.uuid4().hex[:8]}",
        "method": "elicitation/create",
        "params": {
            "message": f"是否允许在真实 TCMS 引擎上执行场景 {scenario_file}？",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "approve": {"type": "boolean", "description": "true=允许执行；false=拒绝"},
                    "note": {"type": "string", "description": "可选备注（会原样回传）"},
                },
                "required": ["approve"],
            },
        },
    }
    try:
        resp = requester(msg)
    except Exception as e:  # noqa: BLE001
        return None, f"审批请求失败（已捕获）: {type(e).__name__}: {e}"
    if not isinstance(resp, dict):
        return None, "审批响应格式非法（应为 JSON-RPC 响应对象）"
    if "error" in resp:
        return None, f"审批请求被客户端拒绝: {resp.get('error')}"
    result = resp.get("result") or {}
    action = str(result.get("action") or "").lower()
    if action not in ("accept", "decline", "cancel"):
        return None, f"审批响应缺少合法 action（收到 {action!r}）"
    return action, None


def _tools_list() -> list[dict]:
    return _READ_TOOLS + [dict(RUN_SCENARIO_SCHEMA)]


def _capabilities() -> dict:
    return {
        "tools": {"listChanged": False},
        "resources": {"subscribe": True, "listChanged": False},
        "prompts": {"listChanged": False},
    }


def _emit(ctx, method: str, params: dict) -> None:
    """发一条 JSON-RPC 通知（transport 未注入时静默跳过）。"""
    notify = getattr(ctx, "notify", None)
    if callable(notify):
        notify({"jsonrpc": "2.0", "method": method, "params": params})


def _call_run_scenario(arguments: dict, ctx: SimpleNamespace, token=None) -> dict:
    """R2 真实执行：四类失败各自成文，绝不把不同原因混成一句话。"""
    if getattr(ctx, "runner", None) is None:
        return _text_err(
            "run_scenario 需要真实 TCMS 引擎 runner 已接线；当前上下文以 with_runner=False 构造，"
            "未接线。只读工具（kb_search/kb_filter_assets/symptom_diagnose/kb_node/list_scenarios）全部可用。"
        )
    file = str((arguments or {}).get("scenario_file") or "").strip()
    if not file:
        return _text_err("run_scenario 需要非空 scenario_file")
    if bool((arguments or {}).get("require_approval")):
        decision, err = request_approval(ctx, file)
        if err:
            return _text_err(err)
        if decision != "accept":
            return _text_err(
                f"用户未批准执行 {file}（elicitation 结果：{decision}）——**未在真实引擎上执行任何动作**。"
            )
    if token is not None:
        _emit(ctx, "notifications/progress", {"progressToken": token, "progress": 0, "total": 2, "message": f"准备执行 {file}"})
    try:
        out = ctx.runner(file)
    except EngineUnavailable as e:
        return _text_err(f"TCMS 引擎不可用：{e}")
    except KeyError:
        return _text_err(f"场景不存在: {file}（先用 list_scenarios 确认可用场景名）")
    except FileNotFoundError as e:
        return _text_err(
            f"场景文件在磁盘上缺失: {e}（资产模型里有这个场景，但 YAML 未找到；"
            f"请检查 TCMS_UPSTREAM_DIR 或所装 tcms-can-test 版本）"
        )
    except Exception as e:  # noqa: BLE001
        return _text_err(f"场景执行失败（已捕获）: {type(e).__name__}: {e}")
    if token is not None:
        _emit(ctx, "notifications/progress", {"progressToken": token, "progress": 1, "total": 2,
                                              "message": f"执行完成 all_passed={out.get('all_passed')}"})
    return _text_ok(out)


def _call_tool(name: str, arguments: dict, ctx: SimpleNamespace, token=None) -> dict:
    """执行工具：返回 MCP 规范的 result{content, isError}。"""
    if name == "run_scenario":
        return _call_run_scenario(arguments, ctx, token=token)
    result = run_tool_safe(name, arguments if isinstance(arguments, dict) else {}, ctx.m, ctx.g, ctx.hr)
    return {"isError": bool(result.get("error")), "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


def _list_resources(params: dict, ctx: SimpleNamespace) -> dict:
    """分页列出资源；有下一页时给 nextCursor（绝不静默截断）。"""
    catalog = resource_catalog(ctx.m)
    raw = (params or {}).get("cursor")
    try:
        start = int(raw) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        start = 0
    start = max(0, start)
    page = catalog[start:start + RESOURCE_PAGE]
    out: dict = {"resources": page}
    if start + RESOURCE_PAGE < len(catalog):
        out["nextCursor"] = str(start + RESOURCE_PAGE)
    return out


def _read_resource(params: dict, ctx: SimpleNamespace):
    uri = str((params or {}).get("uri") or "").strip()
    if not uri:
        return None, "resources/read 需要 uri"
    try:
        mime, text = read_resource(ctx.m, uri)
    except KeyError:
        return None, f"未知资源: {uri}（先用 resources/list 取可用 URI）"
    return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}, None


def dispatch(msg: dict, ctx: SimpleNamespace) -> dict | None:
    """处理一条 JSON-RPC 消息；返回完整应答{jsonrpc,id,result|error}；通知返回 None。"""
    method = msg.get("method")
    params = msg.get("params") or {}
    rid = msg.get("id")
    if method is None or rid is None:
        # 通知：这里只需处理取消（其余通知按规范静默忽略）
        if method == "notifications/cancelled":
            target = (params or {}).get("requestId")
            cancelled = getattr(ctx, "cancelled", None)
            if cancelled is not None and target is not None:
                cancelled.add(target)
        return None
    if rid in getattr(ctx, "cancelled", ()):  # 已取消：规范要求不回包
        return None
    if method == "initialize":
        # 客户端能力（如 elicitation）必须记住：没声明就不能反向请求，更不能假装问过
        ctx.client_capabilities = set((params.get("capabilities") or {}).keys())
        requested = str(params.get("protocolVersion") or "")
        agreed = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL
        return _ok(rid, {
            "protocolVersion": agreed,
            "capabilities": _capabilities(),
            "serverInfo": dict(SERVER_INFO),
            "instructions": "TCMS 资产与真实执行引擎。只读工具可自由使用；run_scenario 会在真实引擎上执行场景。",
        })
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
        token = ((params or {}).get("_meta") or {}).get("progressToken")
        result = _call_tool(name, arguments, ctx, token=token)
        # 执行期间客户端发了 cancelled → 不回包（规范：MUST NOT respond）。
        # 引擎调用本身是原子的、不可中断，但"结果不返回"是能做到也必须做到的那一半。
        if rid in getattr(ctx, "cancelled", ()):
            return None
        return _ok(rid, result)
    if method == "resources/list":
        return _ok(rid, _list_resources(params, ctx))
    if method == "resources/read":
        result, err = _read_resource(params, ctx)
        if err:
            return _err(rid, -32602, err)
        return _ok(rid, result)
    if method in ("resources/subscribe", "resources/unsubscribe"):
        uri = str((params or {}).get("uri") or "").strip()
        if not uri:
            return _err(rid, -32602, f"{method} 需要 uri")
        result, err = set_subscription(ctx, uri, on=(method == "resources/subscribe"))
        if err:
            return _err(rid, -32602, err)
        return _ok(rid, result)
    if method == "prompts/list":
        return _ok(rid, {"prompts": [
            {"name": n, "description": s["description"], "arguments": s["arguments"]} for n, s in PROMPTS.items()
        ]})
    if method == "prompts/get":
        name = str((params or {}).get("name") or "")
        try:
            return _ok(rid, get_prompt(name, (params or {}).get("arguments") or {}))
        except KeyError:
            return _err(rid, -32602, f"未知 prompt: {name}（先用 prompts/list）")
        except ValueError as e:
            return _err(rid, -32602, str(e))
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

    def notify(obj: dict) -> None:
        stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        stdout.flush()

    if getattr(ctx, "notify", None) is None:
        ctx.notify = notify
    def requester(msg: dict) -> dict:
        """服务端→客户端请求：写出去，然后就地读它的响应（期间到达的其它消息照常处理）。"""
        notify(msg)
        rid = msg.get("id")
        for raw_line in stdin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(obj, dict):
                continue
            if "method" not in obj and obj.get("id") == rid:
                return obj  # 就是我们要的响应
            other = dispatch(obj, ctx)  # 其它请求照常应答，别让客户端空等
            if other is not None:
                notify(other)
        raise TimeoutError("等待客户端审批时 stdin 已关闭")

    if getattr(ctx, "request", None) is None:
        ctx.request = requester
    start_watcher(ctx)  # 订阅了资源才会真正产生通知
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            notify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        resp = dispatch(msg, ctx) if isinstance(msg, dict) else _err(None, -32700, "parse error")
        if resp is not None:
            notify(resp)
    return 0


# ---------------------------------------------------------------------------
# 最小 Streamable HTTP（POST 一个 JSON-RPC 消息 → 一个 JSON 响应）
# ---------------------------------------------------------------------------


def http_jsonrpc(body: bytes, ctx: SimpleNamespace, auth_header: str | None = None, token: str | None = None):
    """纯函数：HTTP 请求体 → (status, payload|None)。通知返回 (202, None)。

    鉴权：配置了 `token` 时要求 `Authorization: Bearer <token>`。
    ⚠️ 未实现 SSE 流式与会话恢复（见模块 docstring 的诚实边界）。
    """
    if token and (auth_header or "").strip() != f"Bearer {token}":
        return 401, {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "未授权：需要 Authorization: Bearer <token>"}}
    try:
        msg = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
    if not isinstance(msg, dict):
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "请求必须是单个 JSON-RPC 对象"}}
    resp = dispatch(msg, ctx)
    return (202, None) if resp is None else (200, resp)


JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}
SSE_HEADERS = {"Content-Type": "text/event-stream; charset=utf-8"}


def _sse_frame(obj: dict) -> str:
    """一条 SSE 帧（MCP 的 Streamable HTTP 用 `event: message` + 单行 data）。"""
    return "event: message\ndata: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _ctx_with_notify(ctx: SimpleNamespace, sink) -> SimpleNamespace:
    """派生一个共享同一模型/订阅状态、但通知写到 sink 的上下文。

    这样并发请求之间互不串台（HTTP 是多线程的），也不必给 dispatch 加参数。
    """
    clone = SimpleNamespace(**vars(ctx))
    clone.notify = sink
    return clone


def http_handle(
    method: str,
    path: str,
    headers: dict | None,
    body: bytes,
    ctx: SimpleNamespace,
    token: str | None = None,
    sessions: set | None = None,
) -> tuple[int, dict, bytes]:
    """纯函数 HTTP 层 → (status, headers, body)。socket 层只做转发。

    - `initialize` 下发 `Mcp-Session-Id`；带未知会话的请求 → 404；`DELETE` 终止会话。
    - `Accept: text/event-stream` → SSE：先逐帧发通知（如 progress），最后发响应帧。
    - 通知类请求（无 id）→ 202 空体。
    """
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    if method == "GET":
        return 405, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32601, "message": "本实现不支持 GET 拉流；请 POST /mcp，并在 Accept 里带 text/event-stream"}},
            ensure_ascii=False).encode("utf-8")
    if method == "DELETE":
        sid = str(h.get("mcp-session-id") or "")
        if sid and sessions is not None and sid not in sessions:
            return 404, dict(JSON_HEADERS), json.dumps(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": f"未知会话: {sid}"}},
                ensure_ascii=False).encode("utf-8")
        if sid and sessions is not None:
            sessions.discard(sid)
        return 204, {}, b""
    if method != "POST":
        return 405, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32601, "message": f"不支持的方法: {method}"}},
            ensure_ascii=False).encode("utf-8")
    if str(path or "").rstrip("/") not in ("", "/mcp"):
        return 404, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32601, "message": f"未知路径: {path}（用 POST /mcp）"}},
            ensure_ascii=False).encode("utf-8")
    if token and str(h.get("authorization") or "").strip() != f"Bearer {token}":
        return 401, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "未授权：需要 Authorization: Bearer <token>"}},
            ensure_ascii=False).encode("utf-8")
    sid = str(h.get("mcp-session-id") or "")
    if sid and sessions is not None and sid not in sessions:
        return 404, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": f"未知会话: {sid}；请重新 initialize"}},
            ensure_ascii=False).encode("utf-8")
    try:
        msg = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return 400, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            ensure_ascii=False).encode("utf-8")
    if not isinstance(msg, dict):
        return 400, dict(JSON_HEADERS), json.dumps(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "请求必须是单个 JSON-RPC 对象"}},
            ensure_ascii=False).encode("utf-8")

    resp_headers = dict(JSON_HEADERS)
    if msg.get("method") == "initialize" and sessions is not None:
        new_sid = uuid.uuid4().hex
        sessions.add(new_sid)
        resp_headers["Mcp-Session-Id"] = new_sid

    wants_sse = "text/event-stream" in str(h.get("accept") or "")
    events: list[dict] = []
    call_ctx = _ctx_with_notify(ctx, events.append) if wants_sse else ctx
    resp = dispatch(msg, call_ctx)

    if resp is None and not events:  # 纯通知且无副作用事件
        return 202, resp_headers, b""
    if not wants_sse:
        if resp is None:
            return 202, resp_headers, b""
        return 200, resp_headers, json.dumps(resp, ensure_ascii=False).encode("utf-8")

    sse_headers = dict(resp_headers)
    sse_headers.update(SSE_HEADERS)
    chunks = [_sse_frame(ev) for ev in events]
    if resp is not None:
        chunks.append(_sse_frame(resp))
    return 200, sse_headers, "".join(chunks).encode("utf-8")


def serve_http(ctx: SimpleNamespace, host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> None:
    """启动 Streamable HTTP 服务（stdlib http.server；每请求一线程，会话表共享）。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sessions: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _respond(self, status: int, headers: dict, payload: bytes) -> None:  # noqa: ANN001
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload or b"")))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def _handle(self, method: str) -> None:  # noqa: ANN001
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, headers, payload = http_handle(
                method, self.path, dict(self.headers.items()), body, ctx, token, sessions
            )
            self._respond(status, headers, payload)

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

        def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
            sys.stderr.write("[mcp-http] " + (fmt % args) + "\n")

    start_watcher(ctx)
    srv = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(
        f"[mcp-http] listening on http://{host}:{port}/mcp (token={'yes' if token else 'no'}, SSE=on)\n"
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]):
    import argparse

    ap = argparse.ArgumentParser(prog="tcms-mcp", description="TCMS MCP server（stdio 默认；--http 走最小 HTTP）")
    ap.add_argument("--http", action="store_true", help="以 HTTP 方式启动（POST /mcp）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default=None, help="HTTP 模式的 Bearer token（不设则不校验）")
    ap.add_argument("--readonly", action="store_true", help="不接真实引擎（run_scenario 返回未接线说明）")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.http:
        ctx = build_context(with_runner=not args.readonly)
        serve_http(ctx, host=args.host, port=args.port, token=args.token)
        raise SystemExit(0)
    ctx = build_context(with_runner=not args.readonly)
    raise SystemExit(serve_stdio(ctx))


def http_main(argv: list[str] | None = None) -> None:
    """console script `tcms-mcp-http`：默认 HTTP 模式。"""
    args = list(sys.argv[1:] if argv is None else argv)
    main(["--http"] + args)


if __name__ == "__main__":
    main()
