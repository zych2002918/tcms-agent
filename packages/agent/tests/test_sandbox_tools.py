"""R1 沙箱写 + 用例真跑：Agent 自己写测试、自己跑、自己修。

最值得看的两类用例：
- **防漂移**：我们对外宣称的 DSL 词表必须与 testgen 编译器的真实白名单一致
  （否则模型照着我们的说明写，却永远编译不过——那是"说明文档害人"）；
- **自我修复**：同一 draft_id 覆盖重写即可完成"失败→改→重跑"，
  这是 Agent 自主反思的最小可验证单元（而原 testgen 是靠写死的 reflect_loop）。
"""

from __future__ import annotations

import json
from pathlib import Path

from tcms_agent.config import AgentConfig
from tcms_agent.graph import build_registry
from tcms_agent.nodes import check_reference
from tcms_agent.permissions import Permission
from tcms_agent.tools.sandbox import DraftSandbox, build_sandbox_tools, dsl_vocabulary

UPSTREAM = Path(__file__).resolve().parents[3] / "engine"


def _reg(knowledge, tmp_path: Path):
    return build_registry(
        knowledge,
        AgentConfig(max_level=Permission.EXECUTE, sandbox_dir=tmp_path / "sandbox"),
        run_id="r1-test",
    )


def _case(name: str = "test_agent_draft", action: str = "derate") -> dict:
    return {
        "name": name,
        "purpose": "验证车门故障触发降级",
        "preconditions": "auto 模式",
        "steps": ["注入车门故障", "查询处置", "与期望比对"],
        "expected": f"door_fault 触发 {action}",
        "tier": "safety",
        "execution": {
            "kind": "fault_scenario",
            "node": "vcu",
            "fault": "door_fault",
            "setup": [],
            "expect": [{"op": "expect_action", "args": {"fault": "door_fault", "action": action}}],
        },
    }


# ---------------------------------------------------------------------------
# 防漂移：自称的词表必须等于编译器白名单
# ---------------------------------------------------------------------------


def test_dsl_vocabulary_is_read_from_testgen_not_hand_copied() -> None:
    """**唯一真源**：dsl_reference 必须直接反映 testgen 的白名单。

    这条测试的来历值得记一笔：最初这里手抄了一份 op 清单，本测试立刻抓到它与
    编译器白名单不一致（漏了 inject_fault / recover_fault）。手抄的"说明书"
    会误导模型写出永远编译不过的用例——所以改成直接读取。
    """
    from tcms_ai_testgen.execution import ASSERT_OPS, EXEC_KINDS
    from tcms_ai_testgen.execution import SETUP_OPS as TG_SETUP

    v = dsl_vocabulary()
    assert set(v["setup_ops"]) == set(TG_SETUP)
    assert set(v["expect_ops"]) == set(ASSERT_OPS)
    assert set(v["kinds"]) == set(EXEC_KINDS)
    reg = _reg_stub()
    ref = reg.invoke("dsl_reference", {})
    assert set(ref["setup_ops"]) == set(TG_SETUP), "工具暴露的词表也必须与真源一致"
    assert set(ref["expect_ops"]) == set(ASSERT_OPS)
    assert set(ref["kinds"]) == set(EXEC_KINDS)


def _reg_stub():
    """只注册 sandbox 工具（不依赖知识底座的真实构建）。"""
    from tcms_agent.tools.registry import ToolRegistry

    class _Ctx:
        upstream = UPSTREAM

    reg = ToolRegistry(max_level=Permission.EXECUTE)
    reg.register_all(
        build_sandbox_tools(_Ctx(), DraftSandbox(root=Path("."), run_id="x"), upstream=UPSTREAM)  # type: ignore[arg-type]
    )
    return reg


def test_kinds_match_compiler_supported_kinds() -> None:
    """kind 必须都是编译器真正支持的（写错 kind 会静默编译失败）。"""
    from tcms_ai_testgen.executor_real import compile_case
    from tcms_ai_testgen.models import GeneratedCase

    for kind in dsl_vocabulary()["kinds"]:
        base = _case(name=f"test_kind_{kind}")
        base["execution"]["kind"] = kind
        # 只保留该 kind 必需的字段，其余按最小可用集
        if kind == "encode_bound":
            base["execution"] = {
                "kind": kind,
                "setup": [],
                "expect": [
                    {
                        "op": "expect_encode_ok",
                        "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 100.0},
                    }
                ],
            }
        elif kind == "simulate_inject":
            base["execution"] = {
                "kind": kind,
                "setup": [{"op": "set_door_state", "args": {"index": 1, "state": 2}}],
                "expect": [
                    {
                        "op": "expect_signal",
                        "args": {"message": "DoorControl", "signal": "Door2State", "equals": "Fault"},
                    }
                ],
            }
        case = GeneratedCase.model_validate(base)
        assert compile_case(case), f"kind={kind} 应能编译出源码"


# ---------------------------------------------------------------------------
# 工具级别
# ---------------------------------------------------------------------------


def test_sandbox_tool_levels(knowledge, tmp_path: Path) -> None:
    tools = {
        t.name: t.level
        for t in build_sandbox_tools(
            knowledge, DraftSandbox(root=tmp_path, run_id="x"), upstream=UPSTREAM
        )
    }
    assert tools["dsl_reference"] == Permission.READ
    assert tools["list_drafts"] == Permission.READ
    assert tools["draft_test_case"] == Permission.SANDBOX
    assert tools["run_draft"] == Permission.EXECUTE


def test_sandbox_tools_hidden_under_read_cap(knowledge, tmp_path: Path) -> None:
    reg = build_registry(
        knowledge, AgentConfig(max_level=Permission.READ, sandbox_dir=tmp_path), run_id="x"
    )
    assert "draft_test_case" not in reg.names()
    r = reg.invoke("draft_test_case", {"case": _case()})
    assert "权限不足" in r["error"]


# ---------------------------------------------------------------------------
# 草稿：校验 / 编译 / 落盘 / 引用
# ---------------------------------------------------------------------------


def test_dsl_reference_lists_real_vocabulary(knowledge, tmp_path: Path) -> None:
    reg = _reg(knowledge, tmp_path)
    r = reg.invoke("dsl_reference", {})
    v = dsl_vocabulary()
    assert set(r["kinds"]) == set(v["kinds"])
    assert set(r["setup_ops"]) == set(v["setup_ops"])
    assert set(r["expect_ops"]) == set(v["expect_ops"])
    assert r["example"]["execution"]["kind"] in v["kinds"], "示例本身必须是可编译的形态"


def test_draft_valid_case_compiles_and_lands_in_sandbox(knowledge, tmp_path: Path) -> None:
    reg = _reg(knowledge, tmp_path)
    r = reg.invoke("draft_test_case", {"case": _case()})
    assert r.get("compiled") is True, r
    sandbox_dir = tmp_path / "sandbox" / "r1-test" / "drafts"
    assert (sandbox_dir / r["case_file"]).is_file()
    assert (sandbox_dir / r["source_file"]).is_file()
    src = (sandbox_dir / r["source_file"]).read_text(encoding="utf-8")
    assert "def test_" in src and "assert " in src, "编译产物应是真实 pytest 源码"


def test_draft_rejects_unknown_op_at_compile_time(knowledge, tmp_path: Path) -> None:
    """未知 op 必须在**写入之前**被拒——不能写进去再让人发现跑不了。"""
    reg = _reg(knowledge, tmp_path)
    bad = _case(name="test_bad_op")
    bad["execution"]["expect"] = [{"op": "expect_teleport", "args": {}}]
    r = reg.invoke("draft_test_case", {"case": bad})
    assert "error" in r
    assert "未知原语" in json.dumps(r, ensure_ascii=False) or "校验失败" in r["error"]
    assert not list((tmp_path / "sandbox").rglob("test_bad_op.*")), "被拒的用例不得落盘"


def test_draft_rejects_missing_required_fields(knowledge, tmp_path: Path) -> None:
    reg = _reg(knowledge, tmp_path)
    r = reg.invoke("draft_test_case", {"case": {"name": "只有名字"}})
    assert "error" in r
    assert "hint" in r, "拒绝时必须给出可行动的引导"


def test_draft_extracts_refs_for_citation_checking(knowledge, tmp_path: Path) -> None:
    """草稿引用的故障键与需求 id 会进引用校验链。"""
    reg = _reg(knowledge, tmp_path)
    case = _case()
    case["covers"] = ["SR-21"]
    r = reg.invoke("draft_test_case", {"case": case})
    assert "fault:door_fault" in r["refs"]
    assert "req:SR-21" in r["refs"]
    assert all(check_reference(x, knowledge)[0] for x in r["refs"])


def test_fabricated_covers_is_caught_by_reference_gate(knowledge, tmp_path: Path) -> None:
    """Agent 自己写的用例若 covers 了不存在的需求，会被引用校验抓出来。"""
    reg = _reg(knowledge, tmp_path)
    case = _case()
    case["covers"] = ["SR-99-does-not-exist"]
    r = reg.invoke("draft_test_case", {"case": case})
    bad = [x for x in r["refs"] if not check_reference(x, knowledge)[0]]
    assert bad == ["req:SR-99-does-not-exist"], "编造的需求引用必须无法通过校验"


# ---------------------------------------------------------------------------
# 真跑 + 自我修复
# ---------------------------------------------------------------------------


def test_run_draft_passes_on_valid_case(knowledge, tmp_path: Path) -> None:
    reg = _reg(knowledge, tmp_path)
    d = reg.invoke("draft_test_case", {"case": _case()})
    r = reg.invoke("run_draft", {"draft_id": d["draft_id"]})
    assert r.get("all_passed") is True, r
    assert r["passed"] == 1 and r["failed"] == 0
    assert r["exec_pass_rate"] == 1.0
    assert "testgen" in r["engine"]


def test_run_draft_reports_readable_failure_for_repair(knowledge, tmp_path: Path) -> None:
    """失败时必须给出**可据以修改**的要点，而不是一整段日志。"""
    reg = _reg(knowledge, tmp_path)
    d = reg.invoke("draft_test_case", {"case": _case(action="emergency_brake")})
    r = reg.invoke("run_draft", {"draft_id": d["draft_id"]})
    assert r["all_passed"] is False
    assert r["failed"] == 1
    lines = " ".join(r.get("failure_lines") or [])
    assert "AssertionError" in lines, f"应含断言失败要点: {lines[:200]}"
    assert "emergency_brake" in lines and "derate" in lines, "要点应显示期望与实际"
    assert r.get("hint"), "应提示如何修复"


def test_self_repair_loop_via_same_draft_id(knowledge, tmp_path: Path) -> None:
    """同一 draft_id 覆盖重写 = Agent 自主完成"失败→改→重跑"。

    这正是原 testgen 用写死的 reflect_loop 做的事，现在由 Agent 自己做。
    """
    reg = _reg(knowledge, tmp_path)
    draft_id = "repair-me"

    d1 = reg.invoke("draft_test_case", {"case": _case(action="emergency_brake"), "draft_id": draft_id})
    assert d1["draft_id"] == draft_id
    r1 = reg.invoke("run_draft", {"draft_id": draft_id})
    assert r1["all_passed"] is False, "第一版应失败"

    d2 = reg.invoke("draft_test_case", {"case": _case(action="derate"), "draft_id": draft_id})
    assert d2["draft_id"] == draft_id, "修复走同一草稿 id（覆盖）"
    r2 = reg.invoke("run_draft", {"draft_id": draft_id})
    assert r2["all_passed"] is True, f"修正后应通过: {r2}"


def test_run_draft_unknown_id_lists_available(knowledge, tmp_path: Path) -> None:
    reg = _reg(knowledge, tmp_path)
    reg.invoke("draft_test_case", {"case": _case()})
    r = reg.invoke("run_draft", {"draft_id": "nope"})
    assert "不存在" in r["error"]
    assert r["available"], "应列出可用草稿，而不是死路"


def test_list_drafts_reflects_sandbox(knowledge, tmp_path: Path) -> None:
    reg = _reg(knowledge, tmp_path)
    assert reg.invoke("list_drafts", {})["count"] == 0
    reg.invoke("draft_test_case", {"case": _case()})
    lst = reg.invoke("list_drafts", {})
    assert lst["count"] == 1
    assert lst["drafts"][0]["compiled"] is True
    assert str(tmp_path) in lst["sandbox"]


def test_drafts_never_touch_the_repo(knowledge, tmp_path: Path) -> None:
    """R1 的核心承诺：只写沙箱，不动仓库。"""
    reg = _reg(knowledge, tmp_path)
    reg.invoke("draft_test_case", {"case": _case(name="test_repo_isolation")})
    repo_root = Path(__file__).resolve().parents[3]  # <repo>/packages/agent/tests/x.py → <repo>
    assert (repo_root / "packages").is_dir(), f"路径推算不对: {repo_root}"
    stray = [p for p in (repo_root / "packages").rglob("test_repo_isolation.*")]
    assert stray == [], f"草稿不得出现在仓库里: {stray}"
