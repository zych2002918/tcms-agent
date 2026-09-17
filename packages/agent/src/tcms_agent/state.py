"""Agent 状态定义（LangGraph StateGraph 的 schema）。

设计要点：
- `messages` 用 LangGraph 的 `add_messages` reducer —— 工具消息自动追加，
  且支持按 id 覆盖，是 ReAct 循环的天然载体。
- `trace` / `evidence` / `sources` 用 `operator.add` reducer —— **累加**语义，
  每次节点返回的增量会被并入总状态。这让"轨迹"成为图的一等产物：
  任何一步都能回溯到「谁在什么时候拿什么证据做了什么决定」。
- `steps` 是显式预算计数器：由 agent 节点自增，条件边据此强制收尾。
  （不依赖 LangGraph 内部计数——预算必须是可审计的显式状态。）
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class TraceEvent(TypedDict):
    """一条审计事件（轨迹的最小单元）。"""

    node: str  # 产生该事件的节点
    event: str  # 事件类型：plan / decide / act / observe / verify / report ...
    detail: str  # 人类可读描述
    step: int  # 当时的步数
    data: dict[str, Any]  # 结构化附加数据


class PlanItem(TypedDict):
    """计划中的一条待办（外化的 TODO，供人审计）。"""

    id: str
    text: str
    status: str  # pending / done / skipped


class AgentState(TypedDict, total=False):
    """一次 Agent 运行的完整状态（会被 checkpoint 逐步持久化）。"""

    # --- 输入 ---
    goal: str
    """用户的自然语言目标，例如「验证车门故障必须触发降级处置」。"""

    run_id: str
    """本次运行的 id（= thread_id）。用于记忆召回时排除自己，避免自引。"""

    # --- 循环载体 ---
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    finished: bool

    # --- 计划（外化，可审计）---
    plan: list[PlanItem]

    # --- 记忆（四层里的"被读到"那一半）---
    memory_context: str
    """召回并渲染好的历史记忆文本块（注入 Agent 提示词）。

    只放**已渲染**的文本，不放原始对象——图状态要可序列化（要落 checkpoint）。"""

    memory_hits: Annotated[list[dict], operator.add]
    """召回明细（结构化，供审计：这次到底想起了什么、相似度多少）。"""

    # --- 审批 ---
    pending: list[dict[str, Any]]
    """待人工审批的工具调用（R3 持久化类）。

    注意：这是**覆盖**语义（无 reducer）——一旦审批完成就被清空，
    避免同一次请求被反复审批。"""

    approvals: Annotated[list[dict], operator.add]
    """审批记录（一次审批一条，含批准/拒绝与理由）。也是审计证据的一部分。"""

    # --- 证据与引用 ---
    evidence: Annotated[list[dict], operator.add]
    """工具返回的证据条目（每条含来源工具与原始结果摘要）。"""

    sources: Annotated[list[str], operator.add]
    """结论引用到的真实资产 id（如 fault:door_fault / req:SR-21）。

    这是「引用强制校验」的原料：verify 节点会逐个核对它们是否真的存在于
    知识底座中——引用幻觉在这里被机器抓住，而不是靠人眼看。"""

    # --- 结果 ---
    verdict: dict[str, Any]
    """终局判定：{passed, reasons, evidence_used, fabricated_refs, ...}"""

    # --- 审计 ---
    trace: Annotated[list[TraceEvent], operator.add]
