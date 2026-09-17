"""手写最小 Agent loop —— 零编排框架（nolib = no library）。

## 这份代码存在的意义

它不是"另一个实现"，而是一台**对照仪器**：用来回答一个具体问题——
**LangGraph 到底替我做了什么？**

为了隔离变量，nolib **故意复用**图中的同一批节点函数、同一个工具注册表、同一个模型：

    复用（与框架版完全相同的对象）        自己实现（框架原本提供的部分）
    ---------------------------------  ----------------------------------
    make_plan_node / make_verify_node   状态合并（reducer 语义：哪些字段累加、哪些覆盖）
    make_agent_node / make_act_node     循环控制（什么时候再问模型、什么时候收尾）
    make_report_node                    预算与终止判定
    ToolRegistry（权限 + 审计）          轨迹记录
    build_chat_model（模型与工具绑定）
    KnowledgeContext

所以两版跑出的**结果应当一致**——如果一致，说明框架的价值不在"让结果更好"，
而在别的地方（持久化、可恢复、可中断、可观测）。这正是我们要用数据说明的事。

## 它主动放弃的（框架真正的价值所在）

- **checkpoint 持久化 / 断点续跑**：进程一挂，整轮轨迹就没了；
- **human-in-the-loop 中断**：模型请求 R3 持久化写入时无法"暂停等人批准"，
  只能当场拒绝（见 `_AUTO_REJECT_REASON`）；
- **流式观测**：只能跑完才看到结果；
- **时间旅行回放**：无法按超级步回看历史状态。

这四条不是"懒得写"，而是**每一个都需要把状态机做得可序列化、可挂起、可恢复**——
那正是编排框架的核心工作。手写版把它们列在门口，而不是假装自己也有。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from ..config import AgentConfig
from ..knowledge import KnowledgeContext, build_knowledge
from ..models import build_chat_model
from ..nodes import (
    make_act_node,
    make_agent_node,
    make_plan_node,
    make_report_node,
    make_verify_node,
)
from ..runner import AgentRunResult
from ..tools.registry import ToolRegistry

#: nolib 无法"暂停等人审批"，因此对需要审批的调用一律当场拒绝。
#: 这不是缺陷的掩盖，而是**能力边界如实呈现**——框架版会真正暂停并等人。
_AUTO_REJECT_REASON = "nolib 无中断能力：需人工审批的持久化写入被自动拒绝（框架版会暂停等人）"

#: 累加语义的字段（对应 LangGraph 里的 operator.add reducer）
_ADDITIVE_FIELDS = ("messages", "trace", "evidence", "sources", "approvals", "memory_hits")


def _merge(state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    """把节点返回的增量并入状态——**这就是 reducer 语义的手写版**。

    LangGraph 让我用 `Annotated[list, operator.add]` 声明"这个字段累加"，
    并在超级步之间自动做这件事。这里必须自己实现，且要记得哪些字段累加、哪些覆盖。
    """
    if not update:
        return state
    out = dict(state)
    for k, v in update.items():
        if k in _ADDITIVE_FIELDS and isinstance(v, list):
            out[k] = list(out.get(k) or []) + v
        else:
            out[k] = v  # 覆盖语义（steps / pending / finished / verdict …）
    return out


def run_goal(
    goal: str,
    *,
    cfg: AgentConfig | None = None,
    knowledge: KnowledgeContext | None = None,
    run_id: str | None = None,
    registry: ToolRegistry | None = None,
) -> AgentRunResult:
    """用一个手写的 while 循环跑完一个目标（无编排框架）。

    返回 `AgentRunResult`——与框架版**同一个类型**，因此可以喂给同一套评测
    （`eval.check`）、同一套指标，做真正的同集对比。
    """
    cfg = cfg or AgentConfig()
    knowledge = knowledge or build_knowledge(cfg.upstream)
    rid = run_id or f"nolib-{uuid.uuid4().hex[:12]}"
    registry = registry or _build_registry(knowledge, cfg, rid)
    model, model_kind = build_chat_model(cfg, knowledge)

    plan_node = make_plan_node(knowledge)
    agent_node = make_agent_node(model, registry, knowledge, cfg)
    act_node = make_act_node(registry, approval_required=cfg.approval_required)
    verify_node = make_verify_node(knowledge)
    report_node = make_report_node()

    state: dict[str, Any] = {
        "goal": goal,
        "run_id": rid,
        "messages": [HumanMessage(goal)],
        "steps": 0,
        "finished": False,
        "plan": [],
        "evidence": [],
        "sources": [],
        "trace": [],
        "pending": [],
        "approvals": [],
        "memory_context": "",
        "memory_hits": [],
    }

    started = time.time()

    # 1) 记忆召回（与框架版同一个节点）
    #
    # 注意：**即使记忆被关闭也照常走这个节点**。框架版的图里 recall 是固定的一环，
    # 关闭时由节点自己产出"已关闭"的轨迹事件。若 nolib 在此处 if 跳过，
    # 两版的轨迹序列就会不一致——对等性测试正是这样抓出来的。
    from ..nodes import make_recall_node

    state = _merge(
        state, make_recall_node(cfg.memory_dir, enabled=cfg.memory_enabled)(state)
    )

    # 2) 计划
    state = _merge(state, plan_node(state))

    # 3) ReAct 循环：问模型 → 若有工具调用则执行 → 再问；否则收尾
    while True:
        state = _merge(state, agent_node(state))
        last = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        calls = list(getattr(last, "tool_calls", None) or []) if last else []
        if not calls:
            break
        update = act_node(state)
        state = _merge(state, update)

        # 需人工审批的调用：nolib 无法暂停 → 当场如实拒绝，并把拒绝回填给模型
        pending = list(update.get("pending") or [])
        if pending:
            state = _merge(state, _reject_pending(state, pending))

    # 4) 校验 + 报告（与框架版同一批节点）
    state = _merge(state, verify_node(state))
    state = _merge(state, report_node(state))

    verdict = dict(state.get("verdict") or {})
    verdict["duration_ms"] = int((time.time() - started) * 1000)
    return AgentRunResult(
        thread_id=rid,
        goal=goal,
        model_kind=model_kind,
        verdict=verdict,
        steps=int(state.get("steps", 0)),
        trace=list(state.get("trace") or []),
        evidence=list(state.get("evidence") or []),
        sources=list(dict.fromkeys(state.get("sources") or [])),
        audit=registry.audit_summary(),
        approvals=list(state.get("approvals") or []),
        memory_hits=list(state.get("memory_hits") or []),
    )


def _reject_pending(state: dict[str, Any], pending: list[dict]) -> dict[str, Any]:
    """把待审批调用一律拒绝（因为 nolib 无法暂停等人）。

    注意 `bound` 变量在此不参与——工具调用已经由 act 节点分流，
    这里只负责补上"被拒绝"的观察结果，否则模型会收到缺答的 tool_calls 而报错。
    """
    import json

    from langchain_core.messages import ToolMessage

    msgs = []
    approvals = []
    for p in pending:
        msgs.append(
            ToolMessage(
                content=json.dumps(
                    {"error": f"人工审批未通过（已拒绝执行）: {_AUTO_REJECT_REASON}",
                     "rejected_by_human": True},
                    ensure_ascii=False,
                ),
                tool_call_id=str(p.get("call_id") or "nolib-reject"),
                name=str(p.get("name") or ""),
            )
        )
        approvals.append(
            {
                "tool": p.get("name"),
                "args": p.get("args"),
                "approved": False,
                "reason": _AUTO_REJECT_REASON,
                "step": int(state.get("steps", 0)),
            }
        )
    return {
        "messages": msgs,
        "approvals": approvals,
        "pending": [],
        "trace": [
            {
                "node": "nolib",
                "event": "rejected",
                "detail": f"nolib 无法暂停：{len(pending)} 项需审批的调用被自动拒绝",
                "step": int(state.get("steps", 0)),
                "data": {},
            }
        ],
    }


def _build_registry(knowledge: KnowledgeContext, cfg: AgentConfig, run_id: str) -> ToolRegistry:
    """复用框架版的注册表构建逻辑（工具面与权限必须完全相同，否则对比无效）。"""
    from ..graph import build_registry

    return build_registry(knowledge, cfg, run_id)


__all__ = ["run_goal"]
