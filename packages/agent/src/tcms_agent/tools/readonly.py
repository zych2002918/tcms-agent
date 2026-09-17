"""R0 只读领域工具。

两条来源：
1. **复用 platform 已验证的工具面**（`tcms_ai_platform.agent.toolassist`）：
   kb_search / kb_filter_assets / symptom_diagnose / kb_node / list_scenarios
   —— 这五个已有真实数据与既有测试覆盖，agent 直接包一层权限与审计即可，
   不重复造轮子（也避免两套实现漂移）。
2. **agent 原生补强**：fault_detail / list_requirements —— 真实测试工程师最常做的
   两件事（查清一个故障的全部处置语义、查需求追溯），platform 的工具面没有直接
   对应的单一入口，因此在这里补齐。两者同样只读、同样源自真实资产。
"""

from __future__ import annotations

from typing import Any, Callable

from ..knowledge import KnowledgeContext
from ..permissions import Permission
from .registry import ToolSpec

# ---------------------------------------------------------------------------
# 工具 1~5：复用 platform 的既有工具面
# ---------------------------------------------------------------------------


def _bind(name: str, runner: Callable[..., dict], ctx: KnowledgeContext) -> Callable[[dict], dict]:
    """把 platform 的 run_tool_safe(name, args, m, g, hr) 绑成单参可调用。"""

    def _call(args: dict[str, Any]) -> dict:
        return runner(name, args, ctx.model, ctx.graph, ctx.retriever)

    _call.__name__ = f"call_{name}"
    return _call


def _platform_tool_specs(ctx: KnowledgeContext) -> list[ToolSpec]:
    from tcms_ai_platform.agent.toolassist import TOOL_SCHEMAS, run_tool_safe

    specs: list[ToolSpec] = []
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        name = str(fn["name"])
        specs.append(
            ToolSpec(
                name=name,
                description=str(fn["description"]),
                level=Permission.READ,
                parameters=dict(fn["parameters"]),
                func=_bind(name, run_tool_safe, ctx),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# 工具 6：fault_detail —— 一个故障的完整处置语义
# ---------------------------------------------------------------------------

_FAULT_DETAIL_PARAMS = {
    "type": "object",
    "properties": {
        "fault_key": {
            "type": "string",
            "description": "故障键（真实 faults.yaml 的 key），如 door_fault / overspeed / eb_failure",
        }
    },
    "required": ["fault_key"],
}


def _fault_detail(ctx: KnowledgeContext, args: dict[str, Any]) -> dict:
    """返回一个故障的完整 FMEA 条目 + 覆盖它的场景（全部来自真实资产）。"""
    key = str(args.get("fault_key") or "").strip()
    if not key:
        return {"error": "fault_detail 需要非空 fault_key"}
    fd = ctx.model.faults_by_key.get(key)
    if fd is None:
        # 诚实失败 + 有用引导（不做模糊匹配猜一个给模型，避免"看起来成功"）
        near = [k for k in ctx.model.faults_by_key if key.lower() in k.lower()][:5]
        return {
            "error": f"故障键不存在（faults.yaml 无此条目）: {key}",
            "hint": "可用 kb_search 或 list_scenarios 找到正确键",
            "near_matches": near,
        }
    covering = [
        {"file": s.file, "name": s.name}
        for s in ctx.model.scenarios.values()
        if key in s.fault_keys
    ]
    # 该故障在图谱中的归属域（enrich 注入的真实域分类）
    domain = ""
    node = ctx.graph.nodes.get(f"fault:{key}")
    if node is not None:
        domain = str((node.props or {}).get("domain") or "")
    return {
        "key": fd.key,
        "name": fd.name,
        "desc": fd.desc,
        "level": fd.level,
        "action": fd.action,
        "sil": getattr(fd, "sil", ""),
        "detect": getattr(fd, "detect", ""),
        "inject": getattr(fd, "inject", ""),
        "recovery": getattr(fd, "recovery", ""),
        "domain": domain,
        "covering_scenarios": covering[:8],
        "covering_scenario_count": len(covering),
        "source": "faults.yaml",
    }


# ---------------------------------------------------------------------------
# 工具 7：list_requirements —— 需求追溯（RTM）
# ---------------------------------------------------------------------------

_LIST_REQ_PARAMS = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "description": "可选关键词（匹配需求 id 或描述文本），如「车门」「SR-07」「超速」",
        },
        "limit": {"type": "integer", "description": "返回上限（默认 20，最大 60）"},
    },
    "required": [],
}


def _requirement_rows(ctx: KnowledgeContext) -> list[dict]:
    """从资产模型里取出需求行（结构防御性处理：不同版本字段可能不同）。"""
    raw = getattr(ctx.model, "requirements", None)
    rows: list[dict] = []
    if isinstance(raw, dict):
        iterable = raw.values()
    elif isinstance(raw, list | tuple):
        iterable = raw
    else:
        return rows
    for item in iterable:
        if isinstance(item, dict):
            row = dict(item)
        else:
            row = {
                k: getattr(item, k, "")
                for k in ("id", "req_id", "text", "desc", "title", "source")
            }
        rows.append(row)
    return rows


def _list_requirements(ctx: KnowledgeContext, args: dict[str, Any]) -> dict:
    """按关键词列出真实需求（RTM），供需求追溯与覆盖度判断。"""
    kw = str(args.get("keyword") or "").strip().lower()
    try:
        limit = max(1, min(int(args.get("limit") or 20), 60))
    except (TypeError, ValueError):
        limit = 20
    rows = _requirement_rows(ctx)
    if kw:
        rows = [r for r in rows if kw in str(r).lower()]
    return {
        "keyword": kw or None,
        "total": len(rows),
        "returned": len(rows[:limit]),
        "items": rows[:limit],
        "source": "tests/rtm.csv",
    }


def _bind_ctx(fn: Callable[[KnowledgeContext, dict], dict], ctx: KnowledgeContext):
    def _call(args: dict[str, Any]) -> dict:
        return fn(ctx, args)

    _call.__name__ = fn.__name__
    return _call


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

_AGENT_NATIVE: list[tuple[str, str, dict, Callable[[KnowledgeContext, dict], dict]]] = [
    (
        "fault_detail",
        "查一个故障的完整 FMEA 语义：等级/处置动作/SIL/检测方式/注入方式/恢复条件，"
        "以及覆盖它的真实场景列表。要写针对某故障的测试前，先用它把处置语义查清楚。",
        _FAULT_DETAIL_PARAMS,
        _fault_detail,
    ),
    (
        "list_requirements",
        "列出/检索真实需求（RTM 追溯矩阵，SR-xx）。做需求覆盖分析、"
        "确认某功能对应哪条需求时用它。",
        _LIST_REQ_PARAMS,
        _list_requirements,
    ),
]


def build_readonly_tools(ctx: KnowledgeContext) -> list[ToolSpec]:
    """构建全部 R0 只读工具（复用的 5 个 + 原生补强的 2 个）。"""
    specs = _platform_tool_specs(ctx)
    for name, desc, params, fn in _AGENT_NATIVE:
        specs.append(
            ToolSpec(
                name=name,
                description=desc,
                level=Permission.READ,
                parameters=params,
                func=_bind_ctx(fn, ctx),
            )
        )
    return specs


__all__ = ["build_readonly_tools"]
