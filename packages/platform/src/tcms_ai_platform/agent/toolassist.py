"""受约束 function-calling（P1-a）：让 LLM 在**真实工具面**内自主查证。

设计（对齐技术深度审计的缺口补位，红线=不发明）：
- 工具面（全部只读/受约束）：kb_search / symptom_diagnose / kb_node /
  list_scenarios —— 执行结果全部来自真实知识底座或确定性诊断，参数由 LLM 提议
  但**经 schema 校验**，工具不存在/执行失败 → 诚实 error 结果回填，绝不编造输出。
- 编排：≤3 轮 chat-with-tools 循环（assistant.tool_calls → 执行 → role=tool 回填）；
  最终必须回到纯文本答复；答复标注使用过的工具（可审计）。
- 降级：无 key / 通道不可用 / 任一轮失败 → llm_generated=false 的确定性引导
  （复用现有 Agent 查证路径的口径，绝不假装调过工具）。
- 语义本体仍在图谱/资产上：工具只返回结构化证据，不给 LLM"自己发明"的机会。
"""

from __future__ import annotations

import json
import traceback

# ---------------------------------------------------------------------------
# 工具 schema（OpenAI 兼容 function calling）
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "kb_filter_assets",
            "description": "按条件枚举真实资产（故障/场景），支持按等级(level)、处置(action)、关键字过滤。"
            "问『什么只是警告/降级但仍能运行』时用它：action=warning/derate 为告警或降级运行类，"
            "action=shutdown/emergency_brake 为停运类；等级 level ∈ critical/major/minor/info。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["fault", "scenario"], "description": "枚举对象：故障字典或复现场景"},
                    "level": {"type": "string", "description": "故障等级过滤（仅 fault）：critical/major/minor/info"},
                    "action": {"type": "string", "description": "处置过滤（仅 fault）：warning/derate/shutdown/emergency_brake/none"},
                    "keyword": {"type": "string", "description": "名称/描述关键字（可选）"},
                    "limit": {"type": "integer", "description": "返回条数上限（默认 10）"},
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "在 TCMS 知识库做混合检索（BM25+向量+图谱证据），返回 top 命中文档。问现象/术语/资产关系前先检索。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "大白话查询，如「车门故障影响发车吗」"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "symptom_diagnose",
            "description": "症状/无码故障多跳诊断（确定性规则）：给候选故障链+验证动作+诚实 no_match。用户描述异常现象时用它。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "症状描述，如「仪表盘闪烁但无故障码」"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_node",
            "description": "查一个知识图谱节点：属性与直接关联邻居（含资产出处）。用户点名某个实体（故障/信号/场景/需求）时用它。",
            "parameters": {
                "type": "object",
                "properties": {"node_id": {"type": "string", "description": "节点 id，如 fault:overspeed / signal:Door1State"}},
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scenarios",
            "description": "列出现成可执行的复现场景（可只列覆盖某故障的）。要找现成场景复现时用它。",
            "parameters": {
                "type": "object",
                "properties": {"fault_key": {"type": "string", "description": "可选故障键过滤，如 overspeed"}},
            },
        },
    },
]

TOOLS_AVAILABLE: list[str] = [s["function"]["name"] for s in TOOL_SCHEMAS]


# ---------------------------------------------------------------------------
# 工具执行（真实知识底座；失败=诚实 error，绝不编造）
# ---------------------------------------------------------------------------


def _fmt(text: str, n: int = 200) -> str:
    return str(text).replace("\n", " ")[:n]


def _filter_assets(m, kind: str, level: str = "", action: str = "", keyword: str = "", limit: int = 10) -> dict:
    """按条件枚举真实资产（fault/scenario）。level/action 仅对 fault 生效。"""
    limit = max(1, min(int(limit or 10), 50))
    kw = (keyword or "").strip().lower()
    lv = (level or "").strip().lower()
    ac = (action or "").strip().lower()
    if kind == "fault":
        out = []
        for f in m.faults_by_key.values():
            if lv and str(f.level or "").lower() != lv:
                continue
            if ac and str(f.action or "").lower() != ac:
                continue
            blob = f"{f.name} {f.desc} {f.key}".lower()
            if kw and kw not in blob:
                continue
            out.append({"key": f.key, "name": f.name, "level": f.level, "action": f.action})
        return {"kind": "fault", "level": lv or None, "action": ac or None, "keyword": keyword or None, "count": len(out), "items": out[:limit]}
    if kind == "scenario":
        if lv or ac:
            return {"error": "scenario 没有 level/action 字段；请对 fault 使用 level/action，或用 keyword 过滤场景名"}
        out = []
        for s in m.scenarios.values():
            blob = f"{s.file} {s.name}".lower()
            if kw and kw not in blob:
                continue
            out.append({"file": s.file, "name": s.name, "fault_keys": sorted(s.fault_keys)[:5]})
        return {"kind": "scenario", "keyword": keyword or None, "count": len(out), "items": out[:limit]}
    return {"error": f"kind 只能是 fault/scenario，收到: {kind}"}


def _run_tool(name: str, args: dict, m, g, hr) -> dict:
    """执行单个工具；返回 JSON 可序列化 dict（含 error 时也是诚实结果）。"""
    if name == "kb_filter_assets":
        kind = str(args.get("kind") or "").strip()
        return _filter_assets(
            m,
            kind,
            level=str(args.get("level") or ""),
            action=str(args.get("action") or ""),
            keyword=str(args.get("keyword") or ""),
            limit=int(args.get("limit") or 10),
        )
    if name == "kb_search":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "kb_search 需要非空 query"}
        res = hr.retrieve_hybrid(query, k=3)
        hits = []
        for h in res.get("hits", [])[:3]:
            hits.append(
                {
                    "doc_id": h["doc_id"],
                    "kind": h["kind"],
                    "score": h["score"],
                    "text": _fmt(h.get("text", ""), 220),
                    "recent_runs": len(h.get("recent_runs") or []),
                }
            )
        return {
            "query": query,
            "routed_domains": res.get("routed_domains") or [],
            "hits": hits,
            "note": "命中为真实资产；nearby 证据见图（可继续用 kb_node 查详情）",
        }
    if name == "symptom_diagnose":
        from .diagnoser import diagnose_symptom

        text = str(args.get("text") or "").strip()
        if not text:
            return {"error": "symptom_diagnose 需要非空 text"}
        r = diagnose_symptom(m, g, hr, text, depth=2, use_llm=False)  # 确定性，不递归
        return {
            "matched": r.get("matched"),
            "no_match": r.get("no_match"),
            "symptom_key": (r.get("symptom") or {}).get("key"),
            "reply": _fmt(r.get("reply", ""), 700),
            "candidates": [
                {"fault": c["fault"], "name": c["name"], "confidence": c["confidence"], "domain": c["domain"]}
                for c in r.get("candidates", [])[:4]
            ],
            "note": "候选全部来自真实故障字典；derived 仅示意（详见 response.reply）",
        }
    if name == "kb_node":
        nid = str(args.get("node_id") or "").strip()
        node = g.nodes.get(nid)
        if node is None:
            return {"error": f"节点不存在（可用 kb_search 找到正确 id）：{nid}"}
        nbs = [
            {"id": nb_id, "kind": g.nodes[nb_id].kind if nb_id in g.nodes else "?", "via": via}
            for nb_id, via in g.neighbors(nid)[:16]
        ]
        return {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "props": {k: str(v)[:120] for k, v in (node.props or {}).items()},
            "neighbors": nbs,
        }
    if name == "list_scenarios":
        fault_key = str(args.get("fault_key") or "").strip() or None
        out = []
        for s in m.scenarios.values():
            if fault_key and fault_key not in s.fault_keys:
                continue
            out.append({"file": s.file, "name": s.name})
            if len(out) >= 40:
                break
        return {"fault_key": fault_key, "scenarios": out, "count": len(out)}
    return {"error": f"未注册工具: {name}"}


def run_tool_safe(name: str, args: dict, m, g, hr) -> dict:
    """带 try/except 的执行壳：任何异常都返回诚实 error 而不是抛出。"""
    try:
        if not isinstance(args, dict):
            return {"error": "工具参数必须为 JSON 对象"}
        return _run_tool(name, args, m, g, hr)
    except Exception as e:  # noqa: BLE001
        return {"error": f"工具执行失败（已捕获，不中断）: {type(e).__name__}: {e}", "trace": traceback.format_exc(limit=2)}


# ---------------------------------------------------------------------------
# 编排：≤3 轮 chat-with-tools
# ---------------------------------------------------------------------------

_SYSTEM = (
    "你是 TCMS 列车软件测试助手。你可以调用下列只读真实工具查证（工具结果全部来自"
    "真实资产/真实诊断，禁止编造）。策略：先检索/诊断拿到证据，再给结论；每次只调用"
    "一个需要的工具；拿到足够证据后停止调用工具，直接用中文回答用户（可引用工具证据"
    "里的 doc_id / fault 键，说明它来自真实知识库）。回答里标注『用了哪个工具』。"
)

_RULE_FALLBACK = (
    "当前未接入可用的 LLM 工具通道（无 API key 或通道不可用），因此没有真正调用工具。"
    "你可以在本页改用自己的目标走「让 Agent 去查证」（检索→真实执行→评审），"
    "或到设置页配置 OpenAI 兼容端点后重试。未实际调用工具时会如实标注。"
)

# “仅告警/降级但仍运行”类问题的确定性枚举（无 LLM 也能答，结果来自真实故障字典）
_WARN_TERMS = ("警告", "告警", "warning", "降级", "derate")
_RUN_TERMS = ("运行", "能跑", "可运行", "还能", "不影响", "继续", "只是", "仅", "失效", "还能跑")


def rule_enum_runnable(m, text: str) -> dict | None:
    """检测『什么失效/故障只是警告/降级但仍能运行』类问题 → 枚举 action∈warning/derate 的故障。"""
    t = (text or "")
    if not any(k in t.lower() or k in t for k in _WARN_TERMS):
        return None
    if not any(k in t.lower() or k in t for k in _RUN_TERMS):
        return None
    try:
        res = _filter_assets(m, "fault", limit=200)
        runnable = [it for it in res["items"] if str(it.get("action", "")).lower() in ("warning", "derate")]
    except Exception:  # noqa: BLE001 - m 不可用时退回通用引导
        return None
    if not runnable:
        return None
    shown = runnable[:8]
    lines = ["（规则确定性枚举，未调用 LLM/未配 key）按故障字典回答："]
    lines.append(
        f"告警但可继续/降级运行的故障共 {len(runnable)} 个"
        f"（action ∈ warning/derate；这类只告警或降级，不会触发停运）。示例："
    )
    for it in shown:
        lines.append(f"  - {it['name']}（{it['key']}，等级 {it['level']}，处置 {it['action']}）")
    lines.append("停运类（shutdown / emergency_brake）不在上列。可用工具 kb_filter_assets 精确过滤，或配 key 后继续推理。")
    return {
        "reply": "\n".join(lines),
        "data": {
            "kind": "rule_enum_warning_derate_runnable",
            "count": len(runnable),
            "shown": shown,
        },
    }


def assist(
    m,
    g,
    hr,
    message: str,
    chat=None,
    max_rounds: int = 3,
    use_llm: bool = True,
) -> dict:
    """主入口：message → {reply, llm_generated, used_tools, rounds, tools_available}。"""
    text = (message or "").strip()
    if not text:
        return {"reply": "请输入要查证的问题。", "llm_generated": False, "used_tools": [], "rounds": 0}
    # 构造 chat（可注入 fake 供测试）
    if chat is None and use_llm:
        try:
            from .llm_backend import LLMAgentBackend, llm_available

            if llm_available():
                chat = LLMAgentBackend()
        except Exception:  # noqa: BLE001
            chat = None
    if chat is None or not callable(getattr(chat, "_chat_tools", None)):
        enum = rule_enum_runnable(m, text)
        if enum:
            base: dict = {"reply": enum["reply"], "llm_generated": False, "used_tools": [], "rounds": 0}
            base["enumeration"] = enum["data"]
            return base
        # 宽泛问法推理（域词×故障句式 → 定向推荐；m 不可用则自然跳过）
        try:
            from ..knowledge.vague import analyze_vague

            vg = analyze_vague(m, text)
        except Exception:  # noqa: BLE001 - m 不可用/无上游时跳过
            vg = None
        if vg:
            base = {"reply": vg["reply"], "llm_generated": False, "used_tools": [], "rounds": 0}
            base["enumeration"] = {
                "kind": vg["kind"],
                "domain": vg["domain_zh"],
                "faults": vg["faults"],
                "scenarios": vg["scenarios"],
            }
            return base
        return {"reply": _RULE_FALLBACK, "llm_generated": False, "used_tools": [], "rounds": 0}

    used: list[str] = []
    extra: list[dict] = []
    rounds = 0
    final_text: str | None = None
    for rnd in range(max(1, min(max_rounds, 5))):
        rounds += 1
        content, calls = chat._chat_tools(_SYSTEM, text, TOOL_SCHEMAS, extra_messages=extra or None)
        if not calls:
            final_text = content or "（模型未给出可答复文本）"
            break
        # 记录 assistant 的 tool_calls 消息（供后续轮上下文）
        extra.append(
            {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls
                ],
            }
        )
        for c in calls:
            name = c["name"]
            if name not in TOOLS_AVAILABLE:
                result = {"error": f"LLM 请求了未开放工具（已拦截，不执行）: {name}"}
            else:
                used.append(name)
                try:
                    args = json.loads(c.get("arguments") or "{}")
                except Exception:  # noqa: BLE001 - 参数非法=诚实拒绝执行
                    result = {"error": "工具参数不是合法 JSON，已拒绝执行"}
                    extra.append(
                        {
                            "role": "tool",
                            "tool_call_id": c.get("id") or f"call_{rnd}_{len(used)}",
                            "content": json.dumps(result, ensure_ascii=False)[:1600],
                        }
                    )
                    continue
                result = run_tool_safe(name, args, m, g, hr)
            extra.append(
                {
                    "role": "tool",
                    "tool_call_id": c.get("id") or f"call_{rnd}_{len(used)}",
                    "content": json.dumps(result, ensure_ascii=False)[:1600],
                }
            )
    if final_text is None:
        final_text = content or "（达到轮次上限仍未收敛；已执行的工具证据在上方。请缩小问题再问一次。）"
    if used:
        final_text += f"\n\n[自证] 本回复实际调用了这些真实工具：{'、'.join(dict.fromkeys(used))}。"
    return {
        "reply": final_text,
        "llm_generated": True,
        "used_tools": list(dict.fromkeys(used)),
        "rounds": rounds,
        "tools_available": list(TOOLS_AVAILABLE),
    }
