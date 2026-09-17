"""human-in-the-loop 审批：R3 持久化写入必须由人放行。

覆盖四件事：
1. 默认（无人审批）→ **拒绝**，且文件真的没写出去（安全默认不是口号）；
2. 批准 → 执行且落盘，审批记录可审计；
3. 拒绝 → 理由如实回填给 Agent（拒绝是一等结果，不是静默丢弃）；
4. 引用门禁独立于审批生效——**人批准了也拦得住幻觉引用**。
"""

from __future__ import annotations

import json
from pathlib import Path

from tcms_agent.config import AgentConfig
from tcms_agent.graph import build_registry
from tcms_agent.nodes import normalize_decisions
from tcms_agent.permissions import Permission
from tcms_agent.runner import AgentRunner
from tcms_agent.tools.persist import PersistentStore, build_persist_tools

GOAL = "验证车门故障必须触发降级处置"


def _write_cfg(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        offline=True,
        max_steps=8,
        max_level=Permission.PERSIST,
        db_path=tmp_path / "cp.sqlite",
        sandbox_dir=tmp_path / "sandbox",
        memory_dir=tmp_path / "memory",
        artifacts_dir=tmp_path / "artifacts",
    )


# ---------------------------------------------------------------------------
# normalize_decisions（审批返回值解析：宽进严出）
# ---------------------------------------------------------------------------


def test_normalize_decisions_accepts_bool_and_dict_and_list() -> None:
    assert normalize_decisions(True, 2) == [
        {"approved": True, "reason": "", "args": None},
        {"approved": True, "reason": "", "args": None},
    ]
    assert normalize_decisions({"approved": False, "reason": "不安全"}, 1)[0]["approved"] is False
    mixed = normalize_decisions([{"approved": True}, {"approved": False, "reason": "x"}], 2)
    assert [d["approved"] for d in mixed] == [True, False]


def test_normalize_decisions_defaults_to_reject_on_garbage() -> None:
    """拿不准就不写：无法解析一律视为拒绝。"""
    assert [d["approved"] for d in normalize_decisions("随便写的", 2)] == [False, False]
    assert [d["approved"] for d in normalize_decisions(None, 1)] == [False]
    # 列表短于待批项时，缺的部分补拒绝
    assert [d["approved"] for d in normalize_decisions([{"approved": True}], 3)] == [
        True,
        False,
        False,
    ]
    assert normalize_decisions(True, 0) == []


# ---------------------------------------------------------------------------
# 端到端：无人审批 → 拒绝
# ---------------------------------------------------------------------------


def test_without_approver_persistence_is_rejected_by_default(tmp_path: Path, knowledge) -> None:
    cfg = _write_cfg(tmp_path)
    res = AgentRunner(cfg, knowledge).run(GOAL, thread_id="t-noapprover")
    assert res.approvals, "应触发过审批"
    assert all(a["approved"] is False for a in res.approvals)
    assert any("默认拒绝" in (a["reason"] or "") for a in res.approvals)
    # 关键：文件真的没写出去
    assert not list((tmp_path / "memory").rglob("*.md")), "未获批准却写了文件 = 安全默认失效"
    assert any(t["event"] == "rejected" for t in res.trace), "轨迹应留下拒绝记录"


def test_default_level_never_triggers_approval(tmp_path: Path, knowledge) -> None:
    """默认档位（R0+R2，不含 R3）下，Agent 根本碰不到持久化工具，也就不会暂停。"""
    cfg = _write_cfg(tmp_path).with_(max_level=Permission.EXECUTE)
    res = AgentRunner(cfg, knowledge).run(GOAL, thread_id="t-default")
    assert res.approvals == [], "默认档位不该触发审批"
    assert not any(t["event"] == "defer" for t in res.trace)
    assert not list((tmp_path / "memory").rglob("*.md"))


# ---------------------------------------------------------------------------
# 端到端：批准 → 执行并落盘
# ---------------------------------------------------------------------------


def test_approved_write_is_executed_and_persisted(tmp_path: Path, knowledge) -> None:
    cfg = _write_cfg(tmp_path)
    seen: list[dict] = []

    def approver(payload: dict) -> dict:
        seen.append(payload)
        return {"approved": True, "reason": "证据充分，同意沉淀"}

    res = AgentRunner(cfg, knowledge).run(GOAL, thread_id="t-approve", approver=approver)

    # 审批载荷是结构化、可审计的
    assert len(seen) == 1
    assert seen[0]["kind"] == "tool_approval"
    assert seen[0]["requests"], "载荷必须列出待审批的调用"
    req = seen[0]["requests"][0]
    assert req["name"] == "write_memory"
    assert req["level_label"] == "R3 持久化"
    assert "refs" in req["args"]

    # 批准后真的落盘了
    files = list((tmp_path / "memory").rglob("*.md"))
    assert files, "批准后应写入长期记忆文件"
    text = files[0].read_text(encoding="utf-8")
    assert "refs_verified" in text, "记忆必须带引用校验标记"
    assert "run_id: \"t-approve\"" in text

    assert [a["approved"] for a in res.approvals] == [True]
    assert any(t["event"] == "approved_exec" for t in res.trace)


# ---------------------------------------------------------------------------
# 端到端：拒绝 → 理由回填
# ---------------------------------------------------------------------------


def test_rejection_reason_is_fed_back_to_agent(tmp_path: Path, knowledge) -> None:
    cfg = _write_cfg(tmp_path)
    res = AgentRunner(
        cfg,
        knowledge,
    ).run(
        GOAL,
        thread_id="t-reject",
        approver=lambda _p: {"approved": False, "reason": "证据还不够，先别写"},
    )
    assert [a["approved"] for a in res.approvals] == [False]
    assert res.approvals[0]["reason"] == "证据还不够，先别写"
    joined = json.dumps(res.evidence, ensure_ascii=False)
    assert "人工审批未通过" in joined, "拒绝必须作为工具结果回填给 Agent"
    assert not list((tmp_path / "memory").rglob("*.md"))


def test_report_discloses_denied_approvals(tmp_path: Path, knowledge) -> None:
    """报告必须披露被拒的写入——否则"✅ 通过"会让人误以为一切都成功了。"""
    cfg = _write_cfg(tmp_path)
    res = AgentRunner(cfg, knowledge).run(
        GOAL, thread_id="t-report", approver=lambda _p: {"approved": False, "reason": "先别写"}
    )
    assert res.verdict["approvals_total"] == 1
    assert res.verdict["approvals_denied"] == 1
    assert "该写入未发生" in res.report
    assert "拒绝 1 项" in res.report


def test_report_omits_approval_line_when_none_triggered(tmp_path: Path, knowledge) -> None:
    cfg = _write_cfg(tmp_path).with_(max_level=Permission.EXECUTE)
    res = AgentRunner(cfg, knowledge).run(GOAL, thread_id="t-noappr")
    assert "人工审批" not in res.report


# ---------------------------------------------------------------------------
# 引用门禁：独立于审批生效
# ---------------------------------------------------------------------------


def test_reference_gate_blocks_hallucinated_memory_even_if_approved(
    tmp_path: Path, knowledge
) -> None:
    """人批准了，但内容引用了不存在的资产 → 机器门禁仍然拒绝。"""
    store = PersistentStore(memory_dir=tmp_path / "memory", artifacts_dir=tmp_path / "artifacts")
    reg = build_registry(
        knowledge,
        AgentConfig(max_level=Permission.PERSIST, sandbox_dir=tmp_path / "sandbox"),
        run_id="gate-test",
    )
    tools = build_persist_tools(knowledge, store, tmp_path / "sandbox", run_id="gate-test")
    write_tool = next(t for t in tools if t.name == "write_memory")

    bad = write_tool.func(
        {
            "title": "幻觉记忆",
            "content": "我编的结论",
            "kind": "semantic",
            "refs": ["fault:door_fault", "fault:this_does_not_exist"],
        }
    )
    assert "写入门禁拒绝" in bad["error"]
    assert bad["rejected_refs"] == ["fault:this_does_not_exist"]
    assert not list((tmp_path / "memory").rglob("*.md")), "门禁拒绝后不得留下文件"

    # 全真实引用则通过
    good = write_tool.func(
        {
            "title": "真实记忆",
            "content": "车门故障触发降级",
            "kind": "semantic",
            "refs": ["fault:door_fault"],
        }
    )
    assert "written" in good, good
    assert list((tmp_path / "memory").rglob("*.md"))
    assert reg.get("write_memory") is not None


def test_write_memory_requires_refs_and_valid_kind(tmp_path: Path, knowledge) -> None:
    store = PersistentStore(memory_dir=tmp_path / "memory", artifacts_dir=tmp_path / "artifacts")
    write_tool = next(
        t
        for t in build_persist_tools(knowledge, store, tmp_path / "sandbox")
        if t.name == "write_memory"
    )
    base = {"title": "t", "content": "c", "kind": "semantic"}
    assert "refs" in write_tool.func({**base, "refs": []})["error"]
    assert "kind" in write_tool.func({**base, "kind": "nonsense", "refs": ["fault:door_fault"]})[
        "error"
    ]
    assert "非空" in write_tool.func({"kind": "semantic", "refs": ["fault:door_fault"]})["error"]
