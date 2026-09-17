"""四层记忆：写 → 召回 → 影响 → 巩固 → 门禁。

本文件里最值得看的三类：
- **闭环**：运行日志 → 巩固 → 技能 → 下一轮运行真的被召回（记忆不是装饰品）；
- **门禁**：拿不出证据运行的"经验"不得进长期记忆（防编造规律被反复强化）；
- **隔离**：测试绝不写用户的真实 ~/.tcms-agent 目录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcms_agent.config import AgentConfig
from tcms_agent.knowledge import KnowledgeContext
from tcms_agent.memory.consolidate import Proposal, consolidate, gate, mine
from tcms_agent.memory.journal import RunJournal, RunRecord
from tcms_agent.memory.recall import build_index, format_memory_context, load_skills
from tcms_agent.nodes import check_reference
from tcms_agent.runner import AgentRunner, AgentRunResult

GOAL_A = "验证车门故障必须触发降级处置"
GOAL_B = "验证车门故障不能发车"


def _rec(run_id: str, goal: str, **kw) -> RunRecord:
    base = {"run_id": run_id, "goal": goal, "passed": True, "steps": 5, "model_kind": "offline-rule"}
    base.update(kw)
    return RunRecord(**base)


# ---------------------------------------------------------------------------
# 运行日志（情景记忆的索引）
# ---------------------------------------------------------------------------


def test_journal_append_and_read_roundtrip(tmp_path: Path) -> None:
    j = RunJournal.under(tmp_path / "memory")
    assert j.read_all() == []
    j.append(_rec("r1", GOAL_A, goal_fault="door_fault", tools_used=["kb_search"]))
    j.append(_rec("r2", GOAL_B, passed=False, reasons=["证据不足"]))
    recs = j.read_all()
    assert [r.run_id for r in recs] == ["r1", "r2"]
    assert recs[1].passed is False and recs[1].reasons == ["证据不足"]
    st = j.stats()
    assert st["runs"] == 2 and st["passed"] == 1 and st["failed"] == 1


def test_journal_survives_corrupt_line(tmp_path: Path) -> None:
    """一行坏数据不该毁掉整段记忆。"""
    j = RunJournal.under(tmp_path / "memory")
    j.append(_rec("r1", GOAL_A))
    with j.path.open("a", encoding="utf-8") as f:
        f.write("{这不是合法 JSON\n")
    j.append(_rec("r2", GOAL_B))
    assert [r.run_id for r in j.read_all()] == ["r1", "r2"]


def test_record_from_result_extracts_fields() -> None:
    res = AgentRunResult(
        thread_id="run-x",
        goal=GOAL_A,
        model_kind="llm",
        verdict={"passed": True, "goal_fault": "door_fault", "evidence_count": 3, "answer": "结论"},
        steps=7,
        evidence=[
            {"tool": "kb_search", "ok": True},
            {"tool": "run_draft", "ok": False, "error": "失败"},
        ],
        sources=["fault:door_fault"],
    )
    rec = RunRecord.from_result(res)
    assert rec.run_id == "run-x" and rec.passed is True and rec.steps == 7
    assert rec.goal_fault == "door_fault"
    assert rec.tools_used == ["kb_search", "run_draft"]
    assert rec.failed_tools == ["run_draft"], "失败工具必须单独记下来（巩固要用）"
    assert rec.refs == ["fault:door_fault"]


def test_run_writes_journal(cfg: AgentConfig, knowledge: KnowledgeContext) -> None:
    runner = AgentRunner(cfg, knowledge)
    runner.run(GOAL_A, thread_id="run-j1")
    recs = RunJournal.under(cfg.memory_dir).read_all()
    assert len(recs) == 1
    assert recs[0].run_id == "run-j1"
    assert recs[0].passed is True
    assert recs[0].goal_fault == "door_fault"


# ---------------------------------------------------------------------------
# 召回
# ---------------------------------------------------------------------------


def test_recall_finds_similar_past_run(tmp_path: Path) -> None:
    idx = build_index(tmp_path / "memory")
    idx.add_episodic(
        [
            _rec("r1", GOAL_A, goal_fault="door_fault"),
            _rec("r2", "验证超速必须触发降级", goal_fault="overspeed"),
        ]
    )
    hits = idx.recall(GOAL_B)
    assert hits, "同族目标应召回"
    assert hits[0].kind == "episodic"
    assert hits[0].id == "r1", "应优先召回与目标字符重合度最高的那条"


def test_recall_excludes_self(tmp_path: Path) -> None:
    """当前运行不能召回自己（否则会自我强化）。"""
    idx = build_index(tmp_path / "memory")
    idx.add_episodic([_rec("me", GOAL_A)])
    assert idx.recall(GOAL_A, include_self="me") == []
    assert idx.recall(GOAL_A) != []


def test_recall_drops_below_threshold(tmp_path: Path) -> None:
    """宁可不召回，也不塞噪音进上下文。"""
    idx = build_index(tmp_path / "memory")
    idx.add_episodic([_rec("r1", "完全不相干的话题甲乙丙丁")])
    assert idx.recall(GOAL_A, min_score=0.5) == []


def test_recall_routes_episodic_and_procedural_separately(tmp_path: Path) -> None:
    idx = build_index(tmp_path / "memory")
    idx.add_episodic([_rec("r1", GOAL_A)])
    idx.add_procedural(
        [{"skill_id": "s1", "title": "车门故障验证套路", "content": "先查字典再真跑"}]
    )
    hits = idx.recall(GOAL_B, k_episodic=2, k_procedural=2)
    kinds = {h.kind for h in hits}
    assert kinds == {"episodic", "procedural"}, f"两类记忆应分别召回: {kinds}"


def test_memory_context_is_bounded_and_labelled(tmp_path: Path) -> None:
    idx = build_index(tmp_path / "memory")
    idx.add_episodic([_rec(f"r{i}", GOAL_A * 20) for i in range(6)])
    ctx = format_memory_context(idx.recall(GOAL_A, k_episodic=6), max_chars=400)
    assert len(ctx) <= 400, "注入的记忆必须是有界的，不能挤爆上下文"
    assert "历史记忆" in ctx
    assert "不得直接照搬" in ctx, "必须提醒模型：记忆不是当前事实，引用仍需核实"


# ---------------------------------------------------------------------------
# 端到端：记忆被召回并注入
# ---------------------------------------------------------------------------


def test_second_run_recalls_first(cfg: AgentConfig, knowledge: KnowledgeContext) -> None:
    runner = AgentRunner(cfg, knowledge)
    a = runner.run(GOAL_A, thread_id="run-A")
    assert a.memory_hits == [], "首次运行没有历史可召回"
    assert any("无相关历史记忆" in t["detail"] for t in a.trace)

    b = runner.run(GOAL_B, thread_id="run-B")
    assert b.memory_hits, "第二次运行应召回第一次"
    assert b.memory_hits[0]["kind"] == "episodic"
    assert any(t["node"] == "recall" and "召回" in t["detail"] for t in b.trace)


def test_memory_context_reaches_the_agent_prompt(knowledge: KnowledgeContext) -> None:
    """召回的文本必须真的进得了系统提示——否则记忆只是记着好看。"""
    from tcms_agent.graph import build_registry
    from tcms_agent.nodes import _system_message

    reg = build_registry(knowledge, AgentConfig())
    msg = _system_message(knowledge, reg, "【历史记忆】上次用 verify_fault_action 成功")
    assert "上次用 verify_fault_action 成功" in msg.content
    plain = _system_message(knowledge, reg, "")
    assert "历史记忆" not in plain.content, "没有记忆时不应出现记忆块"


def test_memory_can_be_disabled_for_ab_testing(cfg: AgentConfig, knowledge: KnowledgeContext) -> None:
    """--no-memory 开关是 R7 做"有记忆 vs 无记忆"对照的基础。"""
    runner = AgentRunner(cfg, knowledge)
    runner.run(GOAL_A, thread_id="run-A")  # 先制造历史
    off = cfg.with_(memory_enabled=False)
    b = AgentRunner(off, knowledge).run(GOAL_B, thread_id="run-B")
    assert b.memory_hits == []
    assert any("已关闭" in t["detail"] for t in b.trace if t["node"] == "recall")


# ---------------------------------------------------------------------------
# 巩固与门禁
# ---------------------------------------------------------------------------


def test_mine_recurring_tool_failure() -> None:
    recs = [
        _rec("r1", GOAL_A, failed_tools=["kb_node"], reasons=["节点不存在"]),
        _rec("r2", GOAL_B, failed_tools=["kb_node"], reasons=["节点不存在"]),
    ]
    props = mine(recs, min_occurrences=2)
    hit = [p for p in props if p.pattern == "recurring_tool_failure"]
    assert hit, f"应挖出反复失败的工具: {[p.pattern for p in props]}"
    assert "kb_node" in hit[0].title
    assert hit[0].evidence == ["r1", "r2"]


def test_mine_proven_tool_path() -> None:
    recs = [
        _rec("r1", GOAL_A, goal_fault="door_fault", tools_used=["kb_search", "run_draft"]),
        _rec("r2", GOAL_B, goal_fault="door_fault", tools_used=["kb_search", "run_draft"]),
    ]
    props = [p for p in mine(recs, min_occurrences=2) if p.pattern == "proven_tool_path"]
    assert props and "door_fault" in props[0].title
    assert props[0].refs == ["fault:door_fault"]


def test_mine_respects_min_occurrences() -> None:
    recs = [_rec("r1", GOAL_A, failed_tools=["kb_node"])]
    assert mine(recs, min_occurrences=3) == [], "只出现一次不足以成为经验"


def test_gate_rejects_proposal_without_evidence(knowledge: KnowledgeContext) -> None:
    """**关键门禁**：拿不出出处运行记录的"经验"不得进长期记忆。"""
    p = Proposal(title="凭空的经验", content="我觉得应该这样", kind="procedural", refs=[], evidence=[])
    ok, rejected = gate([p], check_ref=lambda r: check_reference(r, knowledge), known_run_ids=set())
    assert ok == []
    assert rejected and "没有任何证据运行" in " ".join(rejected[0]["reasons"])


def test_gate_rejects_fabricated_run_ids(knowledge: KnowledgeContext) -> None:
    p = Proposal(title="伪证", content="x", kind="procedural", refs=[], evidence=["run-不存在"])
    ok, rejected = gate(
        [p], check_ref=lambda r: check_reference(r, knowledge), known_run_ids={"run-real"}
    )
    assert ok == []
    assert "证据运行不存在于日志" in " ".join(rejected[0]["reasons"])


def test_gate_rejects_fabricated_asset_refs(knowledge: KnowledgeContext) -> None:
    p = Proposal(
        title="假引用", content="x", kind="procedural", refs=["fault:not_real"], evidence=["run-1"]
    )
    ok, rejected = gate(
        [p], check_ref=lambda r: check_reference(r, knowledge), known_run_ids={"run-1"}
    )
    assert ok == []
    assert "引用不存在的资产" in " ".join(rejected[0]["reasons"])


def test_consolidate_end_to_end_writes_skill(tmp_path: Path, knowledge: KnowledgeContext) -> None:
    """完整闭环：日志 → 巩固 → 技能落盘 → 可被召回。"""
    j = RunJournal.under(tmp_path / "memory")
    for i in range(2):
        j.append(_rec(f"r{i}", GOAL_A, goal_fault="door_fault", tools_used=["kb_search", "run_draft"]))
    res = consolidate(
        tmp_path / "memory",
        check_ref=lambda r: check_reference(r, knowledge),
        min_occurrences=2,
        write=True,
    )
    assert res["runs_scanned"] == 2
    assert res["accepted"] >= 1
    assert res["written"], "通过门禁的提案应落盘"

    skills = load_skills(tmp_path / "memory")
    assert skills, "技能库应能读回"
    assert skills[0]["refs_verified"] is True
    assert skills[0]["source"] == "consolidation"

    idx = build_index(tmp_path / "memory")
    hits = idx.recall(GOAL_A, k_procedural=3)
    assert any(h.kind == "procedural" for h in hits), "巩固出的技能应可被召回"


def test_consolidate_dry_run_does_not_write(tmp_path: Path, knowledge: KnowledgeContext) -> None:
    """默认不写入：先看清提案再落盘是刻意的设计。"""
    j = RunJournal.under(tmp_path / "memory")
    for i in range(2):
        j.append(_rec(f"r{i}", GOAL_A, goal_fault="door_fault", tools_used=["kb_search"]))
    res = consolidate(
        tmp_path / "memory", check_ref=lambda r: check_reference(r, knowledge), min_occurrences=2
    )
    assert res["accepted"] >= 1
    assert res["written"] == []
    assert not (tmp_path / "memory" / "procedural").exists()


@pytest.mark.parametrize("bad", ["", "  "])
def test_recall_with_empty_query_is_safe(bad: str, tmp_path: Path) -> None:
    idx = build_index(tmp_path / "memory")
    idx.add_episodic([_rec("r1", GOAL_A)])
    assert isinstance(idx.recall(bad), list), "空查询不应抛异常"


def test_skill_files_have_auditable_frontmatter(tmp_path: Path, knowledge: KnowledgeContext) -> None:
    j = RunJournal.under(tmp_path / "memory")
    for i in range(2):
        j.append(_rec(f"r{i}", GOAL_A, goal_fault="door_fault", tools_used=["kb_search"]))
    consolidate(
        tmp_path / "memory",
        check_ref=lambda r: check_reference(r, knowledge),
        min_occurrences=2,
        write=True,
    )
    files = list((tmp_path / "memory" / "procedural").glob("*.md"))
    assert files
    meta, body = files[0].read_text(encoding="utf-8").split("\n---\n", 1)
    assert "evidence" in meta and "pattern" in meta and "refs_verified" in meta
    assert body.strip(), "技能正文不能为空"


def test_engineered_example_of_pollution_being_blocked(tmp_path: Path, knowledge: KnowledgeContext) -> None:
    """把"幻觉污染长期记忆"这条风险做成一个可执行的断言。

    场景：某次运行编造了一个不存在的故障键，巩固时若照单全收写进技能，
    今后每次召回都会强化这个错误。门禁必须在入库前拦住。
    """
    j = RunJournal.under(tmp_path / "memory")
    j.append(_rec("r1", GOAL_A, failed_tools=["kb_node"], reasons=["节点不存在"]))
    j.append(_rec("r2", GOAL_B, failed_tools=["kb_node"], reasons=["节点不存在"]))
    res = consolidate(
        tmp_path / "memory",
        check_ref=lambda r: check_reference(r, knowledge),
        min_occurrences=2,
        write=True,
    )
    # 这些运行没有编造故障键，所以应通过；真正的防线是下面的对照：
    for w in res["written"]:
        raw = Path(w).read_text(encoding="utf-8")
        meta = json.loads("{}") if not raw.startswith("---") else None
        assert meta is None  # 仅确认文件是 markdown frontmatter 形态
    # 对照：带假引用的提案一定被拒
    fake = Proposal(title="污染", content="x", kind="procedural", refs=["fault:ghost"], evidence=["r1"])
    ok, rejected = gate(
        [fake], check_ref=lambda r: check_reference(r, knowledge), known_run_ids={"r1", "r2"}
    )
    assert ok == [] and rejected
