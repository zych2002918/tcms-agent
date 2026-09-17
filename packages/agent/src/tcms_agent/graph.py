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
    make_approve_node,
    make_plan_node,
    make_report_node,
    make_verify_node,
)
from .state import AgentState
from .tools.execution import ExecutionSandbox, build_execution_tools
from .tools.persist import PersistentStore, build_persist_tools
from .tools.readonly import build_readonly_tools
from .tools.registry import ToolRegistry
from .tools.sandbox import DraftSandbox, build_sandbox_tools


def build_registry(
    knowledge: KnowledgeContext,
    cfg: AgentConfig,
    run_id: str = "adhoc",
) -> ToolRegistry:
    """构建工具注册表：**注册全量工具面**，由 `cfg.max_level` 做门禁。

    为什么注册全量而不是"按档位选择性注册"：
    选择性注册会让注册表的第二道防线（`invoke()` 拒绝越权调用）永远不被触发——
    变成形同虚设的代码。注册全量后：
      - `schemas()` 只吐允许的级别（模型看不到越权工具）；
      - `invoke()` 对越权调用如实拒绝并记审计（模型绕过 schema 也拦得住）；
      - `describe()` 能如实告诉人"这个工具存在，但当前档位不给用"。
    两道防线都真实生效，这才是"双保险"的本意。

    各级别实现进度：
        R0 只读    ✅ 7 个（复用 platform 5 个 + 原生 2 个）
        R1 沙箱写  ⬜ 计划于 R4（需同时落地沙箱与丢弃机制）
        R2 真实执行 ✅ 2 个（子进程 + 真超时 + 产物归档）
        R3 持久化  ✅ 2 个（write_memory / promote_artifact）+ 人工审批 + 引用门禁
    """
    registry = ToolRegistry(max_level=cfg.max_level)
    registry.register_all(build_readonly_tools(knowledge))  # R0
    sandbox = ExecutionSandbox(root=cfg.sandbox_dir, run_id=run_id)
    registry.register_all(
        build_execution_tools(knowledge, sandbox, timeout_s=cfg.exec_timeout_s)  # R2
    )
    # R1 沙箱写 + 配套的 DSL 参考（R0）与用例真跑（R2）
    drafts = DraftSandbox(root=cfg.sandbox_dir, run_id=run_id)
    registry.register_all(
        build_sandbox_tools(
            knowledge, drafts, upstream=knowledge.upstream, timeout_s=cfg.draft_timeout_s
        )
    )
    store = PersistentStore(memory_dir=cfg.memory_dir, artifacts_dir=cfg.artifacts_dir)
    registry.register_all(
        build_persist_tools(knowledge, store, cfg.sandbox_dir, run_id=run_id)  # R3
    )
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
    run_id: str = "adhoc",
) -> tuple[Any, ToolRegistry, str]:
    """构建并编译 Agent 图。

    返回 (compiled_graph, registry, model_kind)：
    - registry 由调用方持有，用于读取**审计记录**（谁调了什么、耗时、成败）；
    - model_kind ∈ {"llm", "offline-rule"}，必须如实向上汇报，不得混淆。
    """
    registry = build_registry(knowledge, cfg, run_id)
    model, model_kind = build_chat_model(cfg, knowledge)

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node(knowledge))
    g.add_node("agent", make_agent_node(model, registry, knowledge, cfg))
    g.add_node("act", make_act_node(registry, approval_required=cfg.approval_required))
    g.add_node("approve", make_approve_node(registry, run_id=run_id))
    g.add_node("verify", make_verify_node(knowledge))
    g.add_node("report", make_report_node())

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_conditional_edges(
        "agent",
        lambda s: _route_after_agent(s, cfg.max_steps),
        {"act": "act", "verify": "verify"},
    )
    # act 之后分流：有待审批项 → 人工审批节点（唯一的 interrupt 点）；否则回 agent
    g.add_conditional_edges(
        "act",
        lambda s: "approve" if (s.get("pending") or []) else "agent",
        {"approve": "approve", "agent": "agent"},
    )
    g.add_edge("approve", "agent")
    g.add_edge("verify", "report")
    g.add_edge("report", END)

    return g.compile(checkpointer=checkpointer), registry, model_kind


__all__ = ["build_graph", "build_registry"]
