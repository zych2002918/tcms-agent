"""上下文预算：长任务不许悄悄逼近模型窗口。

## 为什么需要

单条工具结果已被 `MAX_RESULT_CHARS` 截到 4000 字符，但 12 步 × 每步最多两条结果，
最坏能堆到近 10 万字符——还没算系统提示与记忆块。超窗的报错发生在**模型侧**，
轨迹里什么都看不出来，于是"跑长任务偶发失败"会变成一个查不动的问题。

## 本文件守的四条约束（每条都对应一个真实失败模式）

1. **不删消息**：`tool_call` 与 `tool_result` 必须成对，删一条会被模型侧直接拒（400），
   且报错位置离病因很远；
2. **system 与最近若干条永不裁**：最后一条 AI 消息是结论，`verify` 正是从它里面抽引用；
3. **只压 `ToolMessage`**：观察可以重取，决策链不可重放；
4. **压不动就如实不压**，不为了"看起来守住了预算"而删东西。

其中第 2/3 条最终要保护的性质是：**压缩不会让引用校验失真**——见文件末尾那条测试，
它把压缩后的消息直接喂给 `verify`，验证仍能通过。
"""

from __future__ import annotations

from dataclasses import replace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tcms_agent.config import AgentConfig
from tcms_agent.graph import build_registry
from tcms_agent.knowledge import KnowledgeContext
from tcms_agent.nodes import _apply_context_budget, make_agent_node, make_verify_node


def _tool(content: str, call_id: str = "c0") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id)


def _big_tools(n: int, size: int = 5000) -> list[ToolMessage]:
    return [_tool("y" * size, f"c{i}") for i in range(n)]


# ---------------------------------------------------------------------------
# 1. 未超预算：一个字都不动
# ---------------------------------------------------------------------------


def test_within_budget_is_untouched() -> None:
    """未触发预算时不应产生新列表、也不应留下 note——"什么都没做"要看得出来。"""
    msgs = [SystemMessage(content="sys"), _tool("x" * 100), AIMessage(content="done")]
    out, note = _apply_context_budget(msgs, 10_000)
    assert out is msgs
    assert note == ""


# ---------------------------------------------------------------------------
# 2. 不删消息、不破坏 tool_call 配对
# ---------------------------------------------------------------------------


def test_trim_never_drops_messages_or_breaks_pairing() -> None:
    msgs = [SystemMessage(content="s"), *_big_tools(8)]
    out, note = _apply_context_budget(msgs, 3000)

    assert note, "超预算时应当留下可核对的 note"
    assert len(out) == len(msgs), "只能压缩内容，不能删消息"
    assert sum(len(str(m.content)) for m in out) < sum(len(str(m.content)) for m in msgs), (
        "压完必须真的变小"
    )
    for a, b in zip(msgs, out):
        assert type(a) is type(b), "消息类型与顺序不得改变"
        if isinstance(b, ToolMessage) and b.content != a.content:
            assert b.tool_call_id == a.tool_call_id, "压缩后必须保留 tool_call_id"


# ---------------------------------------------------------------------------
# 3. system 与最近的消息受保护
# ---------------------------------------------------------------------------


def test_system_and_recent_messages_are_protected() -> None:
    """最后一条 AI 消息是结论，`verify` 要读它——它和它附近的消息一律不许裁。"""
    msgs = [SystemMessage(content="s" * 100), *_big_tools(8)]
    out, _ = _apply_context_budget(msgs, 3000, keep_recent=4)

    assert out[0].content == msgs[0].content, "system 不得被压缩"
    for a, b in zip(msgs[-4:], out[-4:]):
        assert a.content == b.content, "最近 keep_recent 条不得被压缩"


def test_trim_does_not_touch_non_tool_messages() -> None:
    """只压 ToolMessage：决策链不可重放，观察可以重取。"""
    msgs = [
        SystemMessage(content="s" * 300),
        HumanMessage(content="h" * 300),
        _tool("t" * 9000, "c1"),
        AIMessage(content="a" * 300),
        _tool("t" * 9000, "c2"),
    ]
    out, note = _apply_context_budget(msgs, 1000, keep_recent=2)

    assert note
    assert out[0].content == msgs[0].content
    assert out[1].content == msgs[1].content
    assert out[3].content == msgs[3].content


# ---------------------------------------------------------------------------
# 4. 占位符与 note 如实
# ---------------------------------------------------------------------------


def test_placeholder_states_what_is_preserved() -> None:
    """压缩占位符必须说明"引用不受影响"——否则读轨迹的人会以为证据丢了。"""
    msgs = [SystemMessage(content="s"), *_big_tools(5, size=9000)]
    out, _ = _apply_context_budget(msgs, 1000, keep_recent=2)

    compressed = [
        m for m in out if isinstance(m, ToolMessage) and "上下文预算压缩" in str(m.content)
    ]
    assert compressed, "应当有工具结果被压缩"
    assert "sources" in str(compressed[0].content), "占位符要说明引用仍保留在 sources 里"


def test_note_is_truthful_about_how_much_was_trimmed() -> None:
    msgs = [SystemMessage(content="s"), *_big_tools(6, size=5000)]
    out, note = _apply_context_budget(msgs, 2000)

    trimmed = sum(
        1
        for a, b in zip(msgs, out)
        if isinstance(a, ToolMessage) and isinstance(b, ToolMessage) and a.content != b.content
    )
    assert f"压缩 {trimmed} 条" in note, note
    assert "上下文预算 2000" in note, note


def test_over_budget_after_trim_is_reported_honestly() -> None:
    """受保护的消息本身就超预算时，note 必须如实说"压缩后仍超"。

    不说的后果很具体：读轨迹的人会以为长任务已经安全，而实际上下一次超窗
    仍会发生在模型侧、仍查不动。
    """
    msgs = [SystemMessage(content="s" * 50), *_big_tools(6, size=9000)]
    out, note = _apply_context_budget(msgs, 1000, keep_recent=4)

    assert "压缩后仍约" in note, note
    assert "受保护的消息本身已超预算" in note, note
    assert len(out) == len(msgs)


# ---------------------------------------------------------------------------
# 5. 端到端：节点把压缩写进轨迹
# ---------------------------------------------------------------------------


class _FakeModel:
    """最小可用模型替身：只回一句结论，不调工具。"""

    def bind_tools(self, schemas):  # noqa: ANN001, ANN201
        return self

    def invoke(self, msgs):  # noqa: ANN001, ANN201
        return AIMessage(content="结论：fault:door_fault")


def test_agent_node_traces_the_compression(
    knowledge: KnowledgeContext, cfg: AgentConfig
) -> None:
    """压缩必须可观测——否则"为什么这次结论变差了"无从查起。"""
    reg = build_registry(knowledge, cfg, run_id="ctx-budget-test")
    node = make_agent_node(_FakeModel(), reg, knowledge, replace(cfg, max_context_chars=1500))

    out = node(  # type: ignore[arg-type]
        {
            "steps": 1,
            "messages": [HumanMessage(content="目标"), *_big_tools(5, size=4000)],
            "memory_context": "",
        }
    )
    events = [t["event"] for t in out["trace"]]
    assert "context_budget" in events, f"轨迹里应有 context_budget 事件，实际 {events}"
    detail = next(t["detail"] for t in out["trace"] if t["event"] == "context_budget")
    assert "压缩" in detail and "释放" in detail, detail


# ---------------------------------------------------------------------------
# 6. 最关键的一条：压缩不会让引用校验失真
# ---------------------------------------------------------------------------


def test_compression_cannot_erase_the_evidence_chain(knowledge: KnowledgeContext) -> None:
    """把压缩后的消息直接喂给 `verify`，引用校验仍应通过。

    这是上面第 2/3 条约束最终要保护的性质：`verify` 读的是 `sources`（独立累积字段）
    与**结论正文**，而不是被压缩掉的那些工具结果内容。若哪天有人改成"从 messages 里
    回忆引用"，这条测试会立刻红——而那时线上表现会是"长任务里引用校验莫名失败"。
    """
    msgs = [SystemMessage(content="s"), *_big_tools(6, size=6000)]
    compressed, note = _apply_context_budget(msgs, 1000, keep_recent=1)
    assert note, "本用例前提是确实发生了压缩"

    verify = make_verify_node(knowledge)
    out = verify(  # type: ignore[arg-type]
        {
            "sources": ["fault:door_fault"],
            "evidence": [{"tool": "kb_search", "ok": True}],
            "steps": 1,
            "trace": [{"node": "plan", "data": {"fault": "door_fault"}}],
            "messages": [*compressed, AIMessage(content="结论：fault:door_fault 已由工具确认。")],
        }
    )
    v = out["verdict"]
    assert v["passed"] is True, v["reasons"]
    assert v["refs_fabricated"] == []
