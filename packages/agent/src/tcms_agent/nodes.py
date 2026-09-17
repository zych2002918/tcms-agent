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

from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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


def _system_message(knowledge: KnowledgeContext, registry: ToolRegistry) -> SystemMessage:
    """系统提示：角色 + 纪律 + 本次运行真实可用的工具清单（由注册表生成，不手写）。"""
    tools_txt = "\n".join(
        f"  - {t['name']} [{t['level_label']}] {t['description']}"
        for t in registry.describe()
        if t["allowed"]
    )
    stats = knowledge.stats()
    return SystemMessage(
        content=(
            f"{SYSTEM_PROMPT}\n"
            f"本次可用工具（权限已按级别裁剪）：\n{tools_txt}\n\n"
            f"知识底座规模：{stats['faults']} 个故障 / {stats['scenarios']} 个场景 / "
            f"{stats['graph_nodes']} 个图谱节点（模式 {stats['asset_mode']}）。"
        )
    )


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

        msgs = [_system_message(knowledge, registry), *state["messages"]]
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
            "trace": [_e("agent", ev, detail, step, tool_calls=[c.get("name") for c in calls])],
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
    return list(dict.fromkeys(out))


def make_act_node(registry: ToolRegistry) -> Callable[[AgentState], dict]:
    """执行上一轮决策里的工具调用（全部经注册表：权限+审计+兜底）。"""

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

        for c in calls:
            name = str(c.get("name") or "")
            args = c.get("args") or {}
            call_id = str(c.get("id") or f"call-{step}-{name}")
            result = registry.invoke(name, args)
            err = str(result.get("error") or "")
            tool_msgs.append(
                ToolMessage(
                    content=registry.render_result(result),
                    tool_call_id=call_id,
                    name=name,
                )
            )
            found = _sources_from(name, result)
            sources += found
            evidence.append(
                {
                    "tool": name,
                    "args": args,
                    "ok": not err,
                    "error": err,
                    "sources": found,
                    "result_digest": registry.audit[-1].result_digest if registry.audit else "",
                }
            )
            trace.append(
                _e(
                    "act",
                    "observe",
                    f"{name} → {'失败: ' + err if err else f'成功，引用 {len(found)} 个资产'}",
                    step,
                    tool=name,
                    args=args,
                    ok=not err,
                )
            )
        return {
            "messages": tool_msgs,
            "evidence": evidence,
            "sources": sources,
            "trace": trace,
        }

    return act_node


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


def make_verify_node(knowledge: KnowledgeContext) -> Callable[[AgentState], dict]:
    """校验：引用真实性 + 目标故障是否被证据覆盖 + 是否有可用证据。"""

    def verify_node(state: AgentState) -> dict:
        sources = list(dict.fromkeys(state.get("sources") or []))
        checked: list[dict] = []
        fabricated: list[str] = []
        for ref in sources:
            ok, how = check_reference(ref, knowledge)
            checked.append({"ref": ref, "exists": ok, "via": how})
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
        if fabricated:
            reasons.append(f"引用了不存在的资产（幻觉引用）：{'、'.join(fabricated)}")
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
            "refs_fabricated": fabricated,
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
                    f"引用校验 {len(checked)} 条，疑似幻觉 {len(fabricated)} 条 → "
                    f"{'通过' if passed else '未通过'}",
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
        head = "✅ 通过" if v.get("passed") else "❌ 未通过"
        lines = [
            f"{head} —— 引用 {v.get('refs_checked', 0)} 条资产，"
            f"工具证据 {v.get('evidence_count', 0)} 条，决策 {v.get('steps_used', 0)} 步"
        ]
        if v.get("reasons"):
            lines.append("未通过原因：" + "；".join(v["reasons"]))
        lines += ["", "【Agent 结论】", answer or "（无最终答复）"]
        v["report"] = "\n".join(lines)
        return {"verdict": v, "finished": True, "trace": [_e("report", "report", head, int(state.get("steps", 0)))]}

    return report_node


__all__ = [
    "SYSTEM_PROMPT",
    "check_reference",
    "make_act_node",
    "make_agent_node",
    "make_plan_node",
    "make_report_node",
    "make_verify_node",
]
