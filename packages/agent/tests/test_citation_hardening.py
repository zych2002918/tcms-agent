"""引用强制校验的**强化**：结论正文里的引用也进入校验链。

## 修的是什么漏洞

此前 `verify` 只校验"工具返回过的 id"（`state.sources`）。这意味着：

> 模型只要在**结论正文**里写一个从未被任何工具返回过的 `fault:xxx`，
> 那条引用根本不进校验链——**幻觉只要不经过工具就查不出来**。

这是"引用强制校验"最容易自欺的地方：校验器看起来很严，实际只覆盖了半条链路。
现在正文里的 `kind:key` 会被正则抽出来，与工具链的引用一起核对，
并在 verdict 里区分来源（`from: tool` / `from: answer_text`）。
"""

from __future__ import annotations

from tcms_agent.config import AgentConfig
from tcms_agent.knowledge import KnowledgeContext
from tcms_agent.nodes import extract_citations
from tcms_agent.runner import AgentRunner

# ---------------------------------------------------------------------------
# 抽取器
# ---------------------------------------------------------------------------


def test_extract_citations_finds_asset_refs() -> None:
    text = "依据 fault:door_fault 与 req:SR-21，场景 scenario:door_cascade.yaml 可用。"
    assert extract_citations(text) == [
        "fault:door_fault",
        "req:SR-21",
        "scenario:door_cascade.yaml",
    ]


def test_extract_citations_dedupes_and_handles_many_kinds() -> None:
    text = "signal:Door2State message:DoorControl system:SYS-DOOR fault:x fault:x"
    assert extract_citations(text) == [
        "signal:Door2State",
        "message:DoorControl",
        "system:SYS-DOOR",
        "fault:x",
    ]


def test_extract_citations_ignores_prose_and_urls() -> None:
    """不能把普通冒号文本误当引用（否则会制造假阳性，反过来伤害可信度）。"""
    assert extract_citations("注意：这里没有引用") == []
    assert extract_citations("https://example.com/a:b") == []
    assert extract_citations("见 sr-21（大小写不同、无前缀）") == []


def test_extract_citations_empty_input() -> None:
    assert extract_citations("") == []


# ---------------------------------------------------------------------------
# 漏洞本身：正文里的幻觉引用必须被抓住
# ---------------------------------------------------------------------------


def test_fabricated_ref_in_answer_text_is_caught(knowledge: KnowledgeContext) -> None:
    """把漏洞做成可执行断言：正文里编造的故障键必须让校验失败。

    构造一个"工具链完全干净、但结论正文写了不存在的故障键"的状态。
    """
    from tcms_agent.nodes import make_verify_node

    verify = make_verify_node(knowledge)
    state = {
        "sources": ["fault:door_fault"],
        "evidence": [{"tool": "fault_detail", "ok": True}],
        "steps": 1,
        "trace": [{"node": "plan", "data": {"fault": "door_fault"}}],
        "messages": [_ai("结论：依据 fault:door_fault 与 fault:ghost_fault_xyz 可以判定。")],
    }
    out = verify(state)  # type: ignore[arg-type]
    v = out["verdict"]
    assert "fault:ghost_fault_xyz" in v["refs_fabricated_in_answer"]
    assert v["passed"] is False
    assert any("结论正文" in r for r in v["reasons"])


def test_clean_answer_text_still_passes(knowledge: KnowledgeContext) -> None:
    """正文引用的都是真实资产时不应误杀。"""
    from tcms_agent.nodes import make_verify_node

    verify = make_verify_node(knowledge)
    state = {
        "sources": ["fault:door_fault"],
        "evidence": [{"tool": "fault_detail", "ok": True}],
        "steps": 1,
        "trace": [{"node": "plan", "data": {"fault": "door_fault"}}],
        "messages": [_ai("结论：fault:door_fault 的处置为 derate，参见 req:SR-21。")],
    }
    out = verify(state)  # type: ignore[arg-type]
    v = out["verdict"]
    assert v["passed"] is True, v["reasons"]
    assert v["refs_fabricated_in_answer"] == []
    assert v["refs_from_answer"] >= 1, "正文引用应被计入校验范围"


def test_verify_records_ref_origin(knowledge: KnowledgeContext) -> None:
    """verdict 要能区分引用来自工具还是正文——否则无法定位幻觉产生在哪一环。"""
    from tcms_agent.nodes import make_verify_node

    verify = make_verify_node(knowledge)
    state = {
        "sources": ["fault:door_fault"],
        "evidence": [{"tool": "fault_detail", "ok": True}],
        "steps": 1,
        "trace": [{"node": "plan", "data": {"fault": "door_fault"}}],
        "messages": [_ai("结论：见 req:SR-21。")],
    }
    v = verify(state)["verdict"]  # type: ignore[arg-type]
    origins = {c["ref"]: c["from"] for c in v["refs"]}
    assert origins["fault:door_fault"] == "tool"
    assert origins["req:SR-21"] == "answer_text"


def test_trace_reports_answer_refs_count(cfg: AgentConfig, knowledge: KnowledgeContext) -> None:
    """端到端：轨迹里应能看到"结论正文"被纳入了校验范围。"""
    res = AgentRunner(cfg, knowledge).run("验证车门故障必须触发降级处置", thread_id="t-refs")
    line = next(t for t in res.trace if t["node"] == "verify")
    assert "含结论正文" in line["detail"], line["detail"]


def _ai(content: str):
    from langchain_core.messages import AIMessage

    return AIMessage(content=content)
