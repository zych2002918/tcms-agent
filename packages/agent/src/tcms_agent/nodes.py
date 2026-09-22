"""图节点实现。

节点职责（每个节点只做一件事，便于单独测试与替换）：

    plan    解析目标 → 外化 TODO 清单 + 锚定目标故障（规则，复用真实资产）
    agent   LLM 决策：调用哪个工具 / 还是收尾（唯一的"自由决策"节点）
    act     经注册表执行工具调用（权限门禁 + 审计 + 观察回填）
    verify  **引用强制校验**：结论引用的每个资产 id 都回真实图谱核对
    report  汇总终局判定与人类可读报告

设计取舍说明：
- plan 在 M1 用**规则**而非 LLM：目标→(故障键, 期望处置) 的映射必须锚定
  真实 fault.yaml，规则解析（platform 的 freeform）已经做到且可复现；让 LLM
  来猜故障键反而引入幻觉面。LLM 规划留到工具面变宽（有写工具）之后才有意义。
- verify 是本项目最有价值的一环：**把"引用幻觉"变成机器可抓的错误**。
  LLM 说得再漂亮，只要它引用了一个不存在的 fault:xxx，verdict 就会失败。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from .config import AgentConfig
from .knowledge import KnowledgeContext
from .state import AgentState, PlanItem, TraceEvent
from .tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是 TCMS（列车网络控制系统）测试工程师 Agent。

你的任务：用**只读工具**在真实知识底座上查证，最终给出有据可依的结论。

纪律（违反即为失败）：
1. 只使用工具返回的真实数据。**禁止编造**故障键、需求编号、场景文件名。
2. 结论必须显式列出"引用资产"，格式为 `fault:<键>` 或 `req:<SR-xx>` 或
   `scenario:<文件名>`，且这些 id 必须来自工具返回结果。
3. 证据不足时如实说明"无法确定"，不要猜一个看起来合理的答案。
4. 先检索、后定论；每轮调用一个最必要的工具，拿到足够证据就停止调用工具并作答。

建议路径：kb_search 找线索 → fault_detail 查清处置语义 → list_scenarios 找复现场景。
"""


def _system_message(
    knowledge: KnowledgeContext, registry: ToolRegistry, memory_context: str = ""
) -> SystemMessage:
    """系统提示：角色 + 纪律 + 本次真实可用的工具清单 + 召回的历史记忆。

    工具清单由注册表生成（不手写）；记忆块由召回节点渲染（空则不出现）。
    """
    tools_txt = "\n".join(
        f"  - {t['name']} [{t['level_label']}] {t['description']}"
        for t in registry.describe()
        if t["allowed"]
    )
    stats = knowledge.stats()
    body = (
        f"{SYSTEM_PROMPT}\n"
        f"本次可用工具（权限已按级别裁剪）：\n{tools_txt}\n\n"
        f"知识底座规模：{stats['faults']} 个故障 / {stats['scenarios']} 个场景 / "
        f"{stats['graph_nodes']} 个图谱节点（模式 {stats['asset_mode']}）。"
    )
    if memory_context:
        body += f"\n\n{memory_context}"
    return SystemMessage(content=body)


# ---------------------------------------------------------------------------
# recall —— 记忆召回（四层记忆的"读"侧）
# ---------------------------------------------------------------------------


def make_recall_node(memory_dir: Any, *, enabled: bool = True) -> Callable[[AgentState], dict]:
    """按当前目标召回历史运行与沉淀技能，渲染成注入提示词的文本块。

    设计说明：
    - **每次运行重建索引**：日志与技能都是小体量本地文件，重建比维护缓存更不容易出错，
      也保证"刚写完的记忆下一次运行就能用上"。体量上来后再加缓存。
    - **排除自己**：`include_self` 传当前 run_id，避免召回本次运行（否则会自我强化）。
    - 召回为空时明确写入"无历史记忆"，而不是留空——让轨迹能区分"没想起来"与"没做召回"。
    """

    def recall_node(state: AgentState) -> dict:
        step = int(state.get("steps", 0))
        goal = state.get("goal", "")
        if not enabled:
            return {
                "memory_context": "",
                "trace": [_e("recall", "recall", "记忆召回已关闭（配置 disabled）", step)],
            }
        try:
            from .memory.recall import build_index, format_memory_context

            idx = build_index(Path(memory_dir))
            hits = idx.recall(goal, k_episodic=3, k_procedural=2, include_self=state.get("run_id"))
            ctx = format_memory_context(hits)
            channel = idx.channel
        except Exception as e:  # noqa: BLE001 - 记忆不可用不该阻断任务
            return {
                "memory_context": "",
                "trace": [_e("recall", "recall", f"记忆召回失败（已忽略）: {type(e).__name__}: {e}", step)],
            }
        detail = (
            f"召回 {len(hits)} 条历史记忆"
            f"（过往运行 {sum(1 for h in hits if h.kind == 'episodic')} / "
            f"沉淀技能 {sum(1 for h in hits if h.kind == 'procedural')}）"
            f" · 向量通道 {channel}"
            if hits
            else f"无相关历史记忆（首次遇到这类目标） · 向量通道 {channel}"
        )
        return {
            "memory_context": ctx,
            "memory_hits": [h.to_dict() for h in hits],
            "trace": [_e("recall", "recall", detail, step, hits=[h.id for h in hits])],
        }

    return recall_node


def _e(node: str, event: str, detail: str, step: int, **data: Any) -> TraceEvent:
    return {"node": node, "event": event, "detail": detail, "step": step, "data": data}


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def make_plan_node(knowledge: KnowledgeContext) -> Callable[[AgentState], dict]:
    """解析目标 → 计划清单 + 目标故障锚定（规则；全部落在真实资产上）。"""

    def plan_node(state: AgentState) -> dict:
        goal = state.get("goal", "")
        fault = ""
        expected = ""
        resolver = "none"
        try:
            from tcms_ai_platform.agent.freeform import parse_free_goal

            parsed = parse_free_goal(knowledge.model, goal, seq=1, use_llm=False)
            fault, expected, resolver = parsed.fault, parsed.expected, parsed.resolver
            anchor = f"识别到目标故障 fault:{fault}（期望处置 {expected}，解析器 {resolver}）"
        except Exception as e:  # noqa: BLE001 - 解析不出目标故障不是崩溃，是"需要澄清"
            anchor = f"未能从目标解析出真实故障键（{type(e).__name__}）——将只做检索查证"

        plan: list[PlanItem] = [
            {"id": "P1", "text": "检索知识底座，收集相关资产证据", "status": "pending"},
            {"id": "P2", "text": "确认故障处置语义（等级/动作/检测/复现）", "status": "pending"},
            {"id": "P3", "text": "找出可复现该故障的真实场景", "status": "pending"},
            {"id": "P4", "text": "校验结论引用的资产真实存在", "status": "pending"},
        ]
        return {
            "plan": plan,
            "trace": [_e("plan", "plan", f"目标解析：{anchor}", 0, fault=fault, expected=expected)],
        }

    return plan_node


# ---------------------------------------------------------------------------
# agent（唯一的自由决策节点）
# ---------------------------------------------------------------------------


#: 压缩占位文案：必须如实说明"省了什么、什么没受影响"。
_BUDGET_PLACEHOLDER = (
    "[上下文预算压缩] 本条工具结果原 {n} 字符，已省略；"
    "它返回过的资产引用仍保留在本次运行的 sources 里，引用校验不受影响。"
)


def _message_chars(m: Any) -> int:
    c = getattr(m, "content", "")
    return len(c if isinstance(c, str) else str(c))


def _apply_context_budget(
    messages: list[Any], budget: int, *, keep_recent: int = 4
) -> tuple[list[Any], str]:
    """按字符预算压缩**较早的工具结果**，返回 `(messages, note)`；未触发时 note 为空串。

    四条设计约束，每条都对应一个真实的失败模式：

    1. **不删消息**，只把 content 换成占位摘要 —— `tool_call` 与 `tool_result` 必须成对，
       删掉一条会让下一轮请求被模型侧直接拒掉（400），而报错位置离病因很远；
    2. **system 与最近 `keep_recent` 条永不裁** —— system 承载工具面与纪律；最后一条
       AI 消息是结论，`verify` 正是从它里面抽引用，裁了会让引用校验凭空失真；
    3. **只压 `ToolMessage`**，不动对话与决策 —— 观察可以重取，决策链不可重放；
    4. **压不动就如实不压**（返回空 note），不为了"看起来守住了预算"而删东西。
    """
    if budget <= 0 or not messages:
        return messages, ""
    total = sum(_message_chars(m) for m in messages)
    if total <= budget:
        return messages, ""

    protected = {0} | set(range(max(0, len(messages) - keep_recent), len(messages)))
    out = list(messages)
    freed = 0
    trimmed = 0
    for i, m in enumerate(messages):
        if total - freed <= budget:
            break
        if i in protected or not isinstance(m, ToolMessage):
            continue
        old = _message_chars(m)
        if old <= 200:  # 已经很小，压了只增加噪声
            continue
        placeholder = _BUDGET_PLACEHOLDER.format(n=old)
        out[i] = ToolMessage(content=placeholder, tool_call_id=m.tool_call_id)
        freed += old - len(placeholder)
        trimmed += 1

    if not trimmed:
        return messages, ""
    note = (
        f"上下文预算 {budget} 字符（原 {total}）：压缩 {trimmed} 条较早的工具结果，"
        f"释放约 {freed} 字符"
    )
    remaining = total - freed
    if remaining > budget:
        # 压不动了：受保护的消息（system / 最近若干条）本身就超预算。
        # 如实说明，而不是假装守住了预算——那会让人误以为长任务已经安全。
        note += f"；压缩后仍约 {remaining} 字符（受保护的消息本身已超预算）"
    return out, note


def make_agent_node(
    model: Any, registry: ToolRegistry, knowledge: KnowledgeContext, cfg: AgentConfig
):
    """LLM 决策节点：产出工具调用或最终答复。"""
    bound = model.bind_tools(registry.schemas())

    def agent_node(state: AgentState) -> dict:
        step = int(state.get("steps", 0))
        if step >= cfg.max_steps:
            # 预算耗尽：不再问模型，直接给一个如实的收尾（由 verify 判定不通过）。
            # 注意 steps **不自增**：这不是一次决策，而是"拒绝再做决策"。
            msg = AIMessage(content=f"已达步数预算上限（{cfg.max_steps} 步），停止继续调用工具。")
            return {
                "messages": [msg],
                "trace": [_e("agent", "budget_exhausted", f"步数预算用尽（{step}/{cfg.max_steps}）", step)],
            }

        msgs = [
            _system_message(knowledge, registry, state.get("memory_context", "")),
            *state["messages"],
        ]
        msgs, budget_note = _apply_context_budget(msgs, cfg.max_context_chars)
        ai: AIMessage = bound.invoke(msgs)
        calls = list(getattr(ai, "tool_calls", None) or [])
        if calls:
            names = "、".join(str(c.get("name")) for c in calls)
            detail = f"决定调用工具：{names}"
            ev = "decide"
        else:
            detail = "决定收尾作答（不再调用工具）"
            ev = "finish"
        return {
            "messages": [ai],
            "steps": step + 1,
            "trace": (
                [_e("agent", ev, detail, step, tool_calls=[c.get("name") for c in calls])]
                + ([_e("agent", "context_budget", budget_note, step)] if budget_note else [])
            ),
        }

    return agent_node


# ---------------------------------------------------------------------------
# act
# ---------------------------------------------------------------------------


def _sources_from(tool_name: str, result: dict) -> list[str]:
    """从工具结果里抽取"引用的真实资产 id"（引用校验的原料）。"""
    out: list[str] = []
    if tool_name == "kb_search":
        out += [str(h["doc_id"]) for h in (result.get("hits") or []) if h.get("doc_id")]
    elif tool_name == "fault_detail" and result.get("key"):
        out.append(f"fault:{result['key']}")
    elif tool_name == "list_scenarios":
        out += [f"scenario:{s['file']}" for s in (result.get("scenarios") or []) if s.get("file")]
    elif tool_name == "kb_node" and result.get("id"):
        out.append(str(result["id"]))
    elif tool_name == "symptom_diagnose":
        out += [f"fault:{c['fault']}" for c in (result.get("candidates") or []) if c.get("fault")]
        if result.get("symptom_key"):
            out.append(f"symptom:{result['symptom_key']}")
    elif tool_name == "kb_filter_assets":
        out += [f"fault:{i['key']}" for i in (result.get("items") or []) if i.get("key")]
    elif tool_name == "run_scenario" and result.get("scenario"):
        out.append(f"scenario:{result['scenario']}")
    elif tool_name == "verify_fault_action":
        # 真执行结果同样要过引用校验：引擎跑过的故障键与场景都必须是真实资产
        if result.get("fault"):
            out.append(f"fault:{result['fault']}")
        if result.get("scenario"):
            out.append(f"scenario:{result['scenario']}")
    elif tool_name in ("draft_test_case", "run_draft"):
        # Agent **自己写**的测试所引用的资产同样要能被核实：
        # 写了 covers:["SR-99"]（不存在）会被引用校验抓出来，而不是"写出来就算数"
        out += [str(r) for r in (result.get("refs") or []) if r]
    return list(dict.fromkeys(out))


def _outcome_digest(tool: str, result: dict) -> dict:
    """从工具结果里抽出一小段**机器可判的结论字段**，随证据一起留存。

    为什么需要它：评测（R7）要能回答"这条自造用例到底跑通了没有"这类问题，
    而 `ok=True` 只说明"工具没抛异常"——一个断言失败的自造用例，工具调用本身
    仍然是成功的。只靠 ok 字段会得出错误结论，所以把关键结论显式摘出来。
    """
    if not isinstance(result, dict):
        return {}
    keys_by_tool: dict[str, tuple[str, ...]] = {
        "run_draft": ("all_passed", "passed", "failed", "total", "exec_pass_rate"),
        "draft_test_case": ("draft_id", "compiled"),
        "verify_fault_action": ("verified", "fault", "expected_action", "scenario"),
        "run_scenario": ("scenario", "all_passed", "passed", "failed"),
        "symptom_diagnose": ("matched", "no_match", "symptom_key"),
        "kb_search": ("query",),
        "fault_detail": ("key", "action", "level"),
        "write_memory": ("written", "kind", "refs_verified"),
    }
    out: dict = {}
    for k in keys_by_tool.get(tool, ()):
        if k in result:
            out[k] = result[k]
    if tool == "kb_search":
        out["hits"] = len(result.get("hits") or [])
    if tool == "symptom_diagnose":
        out["candidates"] = len(result.get("candidates") or [])
    return out


def _observe(
    registry: ToolRegistry,
    name: str,
    args: dict,
    call_id: str,
    step: int,
    *,
    node: str = "act",
) -> tuple[ToolMessage, dict, list[str], TraceEvent]:
    """执行一次工具调用并打包成（工具消息 / 证据 / 引用 / 轨迹事件）。"""
    result = registry.invoke(name, args)
    err = str(result.get("error") or "")
    msg = ToolMessage(content=registry.render_result(result), tool_call_id=call_id, name=name)
    found = _sources_from(name, result)
    evidence = {
        "tool": name,
        "args": args,
        "ok": not err,
        "error": err,
        "sources": found,
        "outcome": _outcome_digest(name, result),
        "result_digest": registry.audit[-1].result_digest if registry.audit else "",
    }
    event = _e(
        node,
        "observe",
        f"{name} → {'失败: ' + err if err else f'成功，引用 {len(found)} 个资产'}",
        step,
        tool=name,
        args=args,
        ok=not err,
    )
    return msg, evidence, found, event


def make_act_node(registry: ToolRegistry, *, approval_required: bool = True) -> Callable[[AgentState], dict]:
    """执行上一轮决策里的工具调用（全部经注册表：权限 + 审计 + 兜底）。

    **本节点绝不调用 `interrupt()`**：它会产生真实副作用（执行场景、审计记录），
    而 LangGraph 在 resume 时会让被中断的节点从头重跑。因此需要人工审批的调用
    被分流到独立的 `approve` 节点——那里 interrupt 之前不做任何有副作用的事。

    分流规则：
        level 不需要审批 → 本节点直接执行
        level 需要审批   → 写入 state.pending，交给 approve 节点
    """

    def act_node(state: AgentState) -> dict:
        msgs = state["messages"]
        last = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        calls = list(getattr(last, "tool_calls", None) or []) if last else []
        if not calls:
            return {"trace": [_e("act", "noop", "本轮无工具调用", int(state.get("steps", 0)))]}

        step = int(state.get("steps", 0))
        tool_msgs: list[ToolMessage] = []
        evidence: list[dict] = []
        sources: list[str] = []
        trace: list[TraceEvent] = []
        pending: list[dict] = []

        for c in calls:
            name = str(c.get("name") or "")
            args = c.get("args") or {}
            call_id = str(c.get("id") or f"call-{step}-{name}")
            spec = registry.get(name)
            needs = bool(
                approval_required and spec is not None and spec.level.requires_approval
            )
            if needs:
                pending.append(
                    {
                        "name": name,
                        "args": args,
                        "call_id": call_id,
                        "level": int(spec.level),
                        "level_label": spec.level.label,
                        "step": step,
                    }
                )
                trace.append(
                    _e(
                        "act",
                        "defer",
                        f"{name} 属于 {spec.level.label}，转入人工审批（未执行）",
                        step,
                        tool=name,
                        args=args,
                    )
                )
                continue
            msg, ev, found, event = _observe(registry, name, args, call_id, step)
            tool_msgs.append(msg)
            evidence.append(ev)
            sources += found
            trace.append(event)

        out: dict = {
            "messages": tool_msgs,
            "evidence": evidence,
            "sources": sources,
            "trace": trace,
            "pending": pending,
        }
        return out

    return act_node


# ---------------------------------------------------------------------------
# approve —— human-in-the-loop 审批（唯一的中断点）
# ---------------------------------------------------------------------------

_APPROVAL_NOTE = (
    "以下工具会产生**持久化副作用**（写入长期记忆 / 提升正式归档），"
    "需要人工批准后才会执行。未获批准则拒绝执行，并把拒绝原因如实回填给 Agent。"
)


def normalize_decisions(raw: Any, n: int) -> list[dict]:
    """把审批者的返回规范成 n 条决策。

    容忍多种写法（宽进严出）：True / False / {"approved": bool} / [如上 × n]。
    缺省与非法值一律视为**拒绝**（安全默认：拿不准就不写）。
    """
    if n <= 0:
        return []
    if isinstance(raw, bool):
        items: list[Any] = [{"approved": raw}] * n
    elif isinstance(raw, dict):
        items = [raw] * n
    elif isinstance(raw, list | tuple):
        items = list(raw) + [{"approved": False}] * max(0, n - len(raw))
    else:
        items = [{"approved": False}] * n

    out: list[dict] = []
    for it in items[:n]:
        if isinstance(it, bool):
            out.append({"approved": it, "reason": ""})
        elif isinstance(it, dict):
            out.append(
                {
                    "approved": bool(it.get("approved")),
                    "reason": str(it.get("reason") or ""),
                    "args": it.get("args"),  # 允许审批时修改参数
                }
            )
        else:
            out.append({"approved": False, "reason": "审批返回值无法解析"})
    return out


def make_approve_node(
    registry: ToolRegistry, *, run_id: str = "adhoc"
) -> Callable[[AgentState], dict]:
    """人工审批节点：中断 → 等决策 → 执行获批项 / 拒绝其余。

    **副作用只写在 `interrupt()` 之后**：resume 时本节点会从头重跑，
    interrupt 之前的代码会被执行两次，因此那里只能做无副作用的准备
    （读 state、构造审批载荷）。这条约束已用最小实验验证过。
    """

    def approve_node(state: AgentState) -> dict:
        pending = list(state.get("pending") or [])
        step = int(state.get("steps", 0))
        if not pending:
            return {"trace": [_e("approve", "noop", "无待审批项", step)]}

        # ---- interrupt 之前：只做无副作用的准备 ----
        payload = {
            "kind": "tool_approval",
            "thread_id": run_id,
            "note": _APPROVAL_NOTE,
            "requests": [
                {
                    "name": p.get("name"),
                    "args": p.get("args"),
                    "level_label": p.get("level_label"),
                }
                for p in pending
            ],
        }
        decision = interrupt(payload)  # ← 暂停点：图状态已落盘，可跨进程恢复
        decisions = normalize_decisions(decision, len(pending))

        # ---- interrupt 之后：恢复时才执行，且只执行一次 ----
        tool_msgs: list[ToolMessage] = []
        evidence: list[dict] = []
        sources: list[str] = []
        trace: list[TraceEvent] = []
        approvals: list[dict] = []

        for p, dec in zip(pending, decisions):
            name = str(p.get("name") or "")
            call_id = str(p.get("call_id") or f"approve-{step}-{name}")
            args = dec.get("args") if isinstance(dec.get("args"), dict) else (p.get("args") or {})
            approved = bool(dec.get("approved"))
            approvals.append(
                {
                    "tool": name,
                    "args": args,
                    "approved": approved,
                    "reason": dec.get("reason", ""),
                    "step": step,
                }
            )
            if not approved:
                # 拒绝也是**一等结果**：如实回填，让 Agent 知道"人不同意"，而不是静默丢弃
                reason = dec.get("reason") or "未说明理由"
                result = {
                    "error": f"人工审批未通过（已拒绝执行）: {reason}",
                    "rejected_by_human": True,
                }
                msg = ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=call_id,
                    name=name,
                )
                tool_msgs.append(msg)
                evidence.append({"tool": name, "args": args, "ok": False, "error": result["error"], "sources": []})
                trace.append(_e("approve", "rejected", f"{name} 被人工拒绝：{reason}", step, tool=name))
                continue

            msg, ev, found, event = _observe(registry, name, args, call_id, step, node="approve")
            tool_msgs.append(msg)
            evidence.append(ev)
            sources += found
            trace.append(
                _e(
                    "approve",
                    "approved_exec",
                    f"{name} 获批并执行 → {'成功' if ev['ok'] else '失败'}",
                    step,
                    tool=name,
                )
            )
            trace.append(event)

        return {
            "messages": tool_msgs,
            "evidence": evidence,
            "sources": sources,
            "trace": trace,
            "approvals": approvals,
            "pending": [],  # 覆盖语义：清空，防止重复审批
        }

    return approve_node


# ---------------------------------------------------------------------------
# verify —— 引用强制校验
# ---------------------------------------------------------------------------


def check_reference(ref: str, knowledge: KnowledgeContext) -> tuple[bool, str]:
    """核对单个引用是否真实存在。返回 (是否存在, 说明)。

    支持的引用形式与核对方式：
        fault:<key>        → faults.yaml 的真实键
        req:<SR-xx>        → 图谱节点 requirement:SR-xx
        scenario:<file>    → 场景目录里的真实文件
        symptom:<key>      → 图谱节点 symptom:<key>
        <kind>:<key>       → 图谱节点本身
    """
    if ":" not in ref:
        return False, "引用格式非法（应为 kind:key）"
    kind, key = ref.split(":", 1)
    if kind == "fault":
        return (key in knowledge.model.faults_by_key), "faults.yaml"
    if kind == "scenario":
        hit = any(s.file == key for s in knowledge.model.scenarios.values())
        return hit, "scenarios/"
    if kind == "req":
        nid = f"requirement:{key}"
        return (nid in knowledge.graph.nodes), "图谱 requirement 节点"
    return (ref in knowledge.graph.nodes), "图谱节点"


def extract_citations(text: str) -> list[str]:
    """从**自然语言文本**里抽出资产引用（`kind:key` 形式）。

    为什么需要它：此前引用校验只覆盖"工具返回过的 id"。如果模型在**结论正文**里
    写了一个从未被任何工具返回过的 `fault:xxx`，那条引用根本不进校验链——
    幻觉只要不经过工具就查不出来。这里把正文里的引用也拉进校验。
    """
    import re

    pattern = re.compile(
        r"\b(fault|req|scenario|symptom|signal|message|system|function|device"
        r"|requirement|threshold|hazard|interlock|mechanism|concept|state|mode|standard)"
        r":([A-Za-z0-9_][A-Za-z0-9_.\-]*)"
    )
    out: list[str] = []
    for m in pattern.finditer(text or ""):
        ref = f"{m.group(1)}:{m.group(2)}"
        if ref not in out:
            out.append(ref)
    return out


def make_verify_node(knowledge: KnowledgeContext) -> Callable[[AgentState], dict]:
    """校验：引用真实性（工具链 + 结论正文）+ 目标故障是否被证据覆盖 + 是否有可用证据。"""

    # 全部真实资产 id（供"截断提及"判定用）。节点在每次构图时创建一次，故预计算一次即可。
    all_ids: set[str] = set(knowledge.graph.nodes)
    all_ids |= {f"fault:{k}" for k in knowledge.model.faults_by_key}
    all_ids |= {f"scenario:{s.file}" for s in knowledge.model.scenarios.values()}

    def _truncated_mention(ref: str) -> str | None:
        """ref 是否是某个真实资产 id 的**前缀**（即"写法不全"而非"编造"）。

        为什么需要这个区分：真实资产 id 可能含空格（如 `standard:ISO 11898-1`），
        而正文里的引用由正则抽取，遇空格即截断 → 得到 `standard:ISO`。
        若一律判为幻觉，就会**误杀**正常引用（R7 的评测首轮就抓到了这个假阳性）。
        判据：存在真实 id 以它开头 → 视为截断提及（记为 truncated，不记为 fabricated）。
        """
        cands = [i for i in all_ids if i.startswith(ref) and i != ref]
        return min(cands, key=len) if cands else None

    def verify_node(state: AgentState) -> dict:
        sources = list(dict.fromkeys(state.get("sources") or []))

        # 结论正文里的引用也要查——模型完全可能"顺手"写一个没被工具返回过的 id
        answer_text = ""
        for m in reversed(state.get("messages") or []):
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                c = m.content
                answer_text = c if isinstance(c, str) else str(c)
                break
        answer_refs = [r for r in extract_citations(answer_text) if r not in sources]

        checked: list[dict] = []
        fabricated: list[str] = []
        truncated: list[dict] = []
        for ref in sources:
            ok, how = check_reference(ref, knowledge)
            checked.append({"ref": ref, "exists": ok, "via": how, "from": "tool"})
            if not ok:
                fabricated.append(ref)
        for ref in answer_refs:
            ok, how = check_reference(ref, knowledge)
            if not ok:
                full = _truncated_mention(ref)
                if full is not None:
                    # 写法不全（如被正则截断）≠ 编造：如实记为 truncated，不算幻觉
                    checked.append(
                        {
                            "ref": ref,
                            "exists": True,
                            "via": f"截断提及 → {full}",
                            "from": "answer_text",
                            "truncated_to": full,
                        }
                    )
                    truncated.append({"ref": ref, "full": full})
                    continue
            checked.append({"ref": ref, "exists": ok, "via": how, "from": "answer_text"})
            if not ok:
                fabricated.append(ref)

        # 目标故障是否被覆盖（plan 节点解析出来的锚点）
        goal_fault = ""
        for t in state.get("trace") or []:
            if t.get("node") == "plan" and t.get("data", {}).get("fault"):
                goal_fault = str(t["data"]["fault"])
                break
        fault_covered = bool(goal_fault) and any(
            ref == f"fault:{goal_fault}" for ref in sources
        )
        has_evidence = bool(state.get("evidence"))

        reasons: list[str] = []
        fab_tool = [r for r in fabricated if any(c["ref"] == r and c["from"] == "tool" for c in checked)]
        fab_answer = [r for r in fabricated if r not in fab_tool]
        if fab_tool:
            reasons.append(f"工具链出现不存在的资产：{'、'.join(fab_tool)}")
        if fab_answer:
            reasons.append(
                f"**结论正文**里引用了工具从未返回过的资产（幻觉引用）：{'、'.join(fab_answer)}"
            )
        if not has_evidence:
            reasons.append("整个过程没有取得任何工具证据")
        if not goal_fault:
            reasons.append("未能从目标中解析出真实故障键（目标需要更明确）")
        elif not fault_covered:
            reasons.append(f"目标故障 fault:{goal_fault} 未被任何工具结果覆盖")

        passed = has_evidence and not fabricated and fault_covered
        verdict = {
            "passed": passed,
            "reasons": reasons,
            "refs_checked": len(checked),
            "refs_from_answer": len(answer_refs),
            "refs_fabricated": fabricated,
            "refs_fabricated_in_answer": fab_answer,
            "refs_truncated": truncated,
            "refs": checked,
            "goal_fault": goal_fault,
            "fault_covered": fault_covered,
            "evidence_count": len(state.get("evidence") or []),
        }
        return {
            "verdict": verdict,
            "trace": [
                _e(
                    "verify",
                    "verify",
                    f"引用校验 {len(checked)} 条（含结论正文 {len(answer_refs)} 条），"
                    f"疑似幻觉 {len(fabricated)} 条 → {'通过' if passed else '未通过'}",
                    int(state.get("steps", 0)),
                    fabricated=fabricated,
                )
            ],
        }

    return verify_node


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def make_report_node() -> Callable[[AgentState], dict]:
    """收尾：把终局判定渲染成人可读报告。"""

    def report_node(state: AgentState) -> dict:
        v = dict(state.get("verdict") or {})
        answer = ""
        for m in reversed(state.get("messages") or []):
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                c = m.content
                answer = c if isinstance(c, str) else str(c)
                break
        v["answer"] = answer
        v["steps_used"] = int(state.get("steps", 0))

        # 审批结果必须进报告：否则"目标达成 ✅"会让人误以为 Agent 想做的一切都成功了。
        approvals = list(state.get("approvals") or [])
        denied = [a for a in approvals if not a.get("approved")]
        v["approvals_total"] = len(approvals)
        v["approvals_denied"] = len(denied)

        head = "✅ 通过" if v.get("passed") else "❌ 未通过"
        lines = [
            f"{head} —— 引用 {v.get('refs_checked', 0)} 条资产，"
            f"工具证据 {v.get('evidence_count', 0)} 条，决策 {v.get('steps_used', 0)} 步"
        ]
        if v.get("reasons"):
            lines.append("未通过原因：" + "；".join(v["reasons"]))
        if approvals:
            ok_n = len(approvals) - len(denied)
            lines.append(
                f"人工审批：{len(approvals)} 项，批准 {ok_n} 项，拒绝 {len(denied)} 项"
                + (
                    "（被拒：" + "、".join(str(a.get("tool")) for a in denied) + " —— 该写入未发生）"
                    if denied
                    else ""
                )
            )
        lines += ["", "【Agent 结论】", answer or "（无最终答复）"]
        v["report"] = "\n".join(lines)
        return {
            "verdict": v,
            "finished": True,
            "trace": [_e("report", "report", head, int(state.get("steps", 0)))],
        }

    return report_node


__all__ = [
    "SYSTEM_PROMPT",
    "check_reference",
    "make_act_node",
    "make_agent_node",
    "make_approve_node",
    "make_plan_node",
    "make_report_node",
    "make_verify_node",
    "normalize_decisions",
]
