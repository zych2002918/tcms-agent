"""LangGraph 图组装。

拓扑（ReAct 变体，多了一个真实的"引用校验"收尾）：

    START → plan → agent ─┬─(有工具调用且未超预算)→ act → agent   ← 循环
                          └─(收尾 / 预算耗尽)──────→ verify → report → END

为什么把 verify 做成独立节点而不是塞进 report：
**校验必须是图的一等公民**。它产出结构化的 verdict（引用是否真实、目标故障是否被
覆盖），这个 verdict 既能供人阅读，也能被评测体系直接消费（M6 的指标就取它）。
把校验藏在报告生成里，就没法对"校验本身"做回归测试了。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from .config import AgentConfig
from .knowledge import KnowledgeContext
from .models import build_chat_model
from .nodes import (
    make_act_node,
    make_agent_node,
    make_plan_node,
    make_report_node,
    make_verify_node,
)
from .state import AgentState
from .tools.readonly import build_readonly_tools
from .tools.registry import ToolRegistry


def build_registry(knowledge: KnowledgeContext, cfg: AgentConfig) -> ToolRegistry:
    """按配置的权限上限构建工具注册表。

    M1 只注册 R0 只读工具；R1/R2/R3 的写工具在后续里程碑加入，
    每次加入都必须同时带上对应的护栏（沙箱 / 超时 / 审批）。
    """
    registry = ToolRegistry(max_level=max(cfg.allow_levels))
    registry.register_all(build_readonly_tools(knowledge))
    return registry


def _route_after_agent(state: AgentState, max_steps: int) -> str:
    """条件边：还有工具调用且预算未超 → 执行；否则进入校验。

    注意用的是 `<=`：`steps` 在 agent 节点里已经自增过，所以"本次决策在预算内"
    对应的是 `steps <= max_steps`。若写成 `<`，预算边界上的那次决策会被白白丢弃
    （调用不执行、结论又没证据）——这是测试抓出来的 off-by-one。
    """
    last = None
    for m in reversed(state.get("messages") or []):
        if isinstance(m, AIMessage):
            last = m
            break
    calls = list(getattr(last, "tool_calls", None) or []) if last else []
    if calls and int(state.get("steps", 0)) <= max_steps:
        return "act"
    return "verify"


def build_graph(
    knowledge: KnowledgeContext,
    cfg: AgentConfig,
    checkpointer: Any = None,
) -> tuple[Any, ToolRegistry, str]:
    """构建并编译 Agent 图。

    返回 (compiled_graph, registry, model_kind)：
    - registry 由调用方持有，用于读取**审计记录**（谁调了什么、耗时、成败）；
    - model_kind ∈ {"llm", "offline-rule"}，必须如实向上汇报，不得混淆。
    """
    registry = build_registry(knowledge, cfg)
    model, model_kind = build_chat_model(cfg, knowledge)

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node(knowledge))
    g.add_node("agent", make_agent_node(model, registry, knowledge, cfg))
    g.add_node("act", make_act_node(registry))
    g.add_node("verify", make_verify_node(knowledge))
    g.add_node("report", make_report_node())

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_conditional_edges(
        "agent",
        lambda s: _route_after_agent(s, cfg.max_steps),
        {"act": "act", "verify": "verify"},
    )
    g.add_edge("act", "agent")
    g.add_edge("verify", "report")
    g.add_edge("report", END)

    return g.compile(checkpointer=checkpointer), registry, model_kind


__all__ = ["build_graph", "build_registry"]
