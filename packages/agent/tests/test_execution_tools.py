"""R2 真执行工具：结论必须由**真实引擎**背书。

这些用例会真的启动子进程跑上游 tcms 引擎（每个约 0.3s），是"能干活、能自证"
这一主张的机器证据。
"""

from __future__ import annotations

import json
from pathlib import Path

from tcms_agent.config import AgentConfig
from tcms_agent.graph import build_registry
from tcms_agent.permissions import Permission
from tcms_agent.tools.execution import ExecutionSandbox, build_execution_tools


def _exec_registry(knowledge, cfg: AgentConfig):
    """构造开放到 R2 的注册表。"""
    return build_registry(knowledge, cfg.with_(max_level=Permission.EXECUTE))


# ---------------------------------------------------------------------------
# run_scenario
# ---------------------------------------------------------------------------


def test_run_scenario_returns_real_engine_assertions(knowledge, tmp_path: Path) -> None:
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    r = reg.invoke("run_scenario", {"scenario_file": "door_cascade.yaml"})
    assert "error" not in r, r
    assert r["engine"].startswith("tcms.scenarios")
    assert r["assertions"], "必须返回引擎真实断言"
    faults = {a["fault"] for a in r["assertions"]}
    assert "door_fault" in faults
    for a in r["assertions"]:
        assert a["actual"] in {"derate", "emergency_brake", "shutdown", "warning", "none"}
        assert isinstance(a["passed"], bool)
    assert r["duration_s"] >= 0


def test_run_scenario_archives_artifact(knowledge, tmp_path: Path) -> None:
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    r = reg.invoke("run_scenario", {"scenario_file": "door_cascade.yaml"})
    archived = tmp_path / r["archive"]
    assert archived.is_file(), f"执行产物应落盘归档: {archived}"
    payload = json.loads(archived.read_text(encoding="utf-8"))
    assert payload["scenario"] == "door_cascade.yaml"
    assert "report" in payload or "error" in payload


def test_run_scenario_rejects_unknown_scenario_with_whitelist(knowledge, tmp_path: Path) -> None:
    """场景名必须来自资产白名单——不是拼接路径，杜绝路径穿越。"""
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    for bad in ("../../etc/passwd", "no_such.yaml", "../scenarios/door_cascade.yaml"):
        r = reg.invoke("run_scenario", {"scenario_file": bad})
        assert "error" in r, f"非法场景名必须被拒: {bad}"


def test_run_scenario_empty_name_is_honest_error(knowledge, tmp_path: Path) -> None:
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    assert "非空" in reg.invoke("run_scenario", {"scenario_file": "   "})["error"]


# ---------------------------------------------------------------------------
# verify_fault_action —— 用引擎断言回答"这个故障真的触发这个处置吗"
# ---------------------------------------------------------------------------


def test_verify_fault_action_confirms_from_engine(knowledge, tmp_path: Path) -> None:
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    r = reg.invoke("verify_fault_action", {"fault_key": "door_fault", "expected_action": "derate"})
    assert r["verified"] is True, r
    assert r["fault"] == "door_fault"
    assert r["scenario"], "应给出实际执行并采信的场景"
    assert r["engine_assertions"], "必须附引擎断言作为证据"
    assert "真实引擎" in r["evidence"]
    assert (tmp_path / r["archive"]).is_file()


def test_verify_fault_action_flags_mismatch_with_dictionary(knowledge, tmp_path: Path) -> None:
    """期望与字典不符时如实指出——字典才是真源，不替用户改期望、也不假通过。"""
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    r = reg.invoke(
        "verify_fault_action", {"fault_key": "door_fault", "expected_action": "emergency_brake"}
    )
    assert r["verified"] is False
    assert r["dictionary_action"] == "derate"
    assert "字典" in r["reason"]


def test_verify_fault_action_unknown_fault(knowledge, tmp_path: Path) -> None:
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    r = reg.invoke(
        "verify_fault_action", {"fault_key": "not_a_fault", "expected_action": "derate"}
    )
    assert "不存在" in r["error"]


def test_verify_fault_action_requires_both_args(knowledge, tmp_path: Path) -> None:
    reg = _exec_registry(knowledge, AgentConfig(sandbox_dir=tmp_path))
    assert "需要" in reg.invoke("verify_fault_action", {"fault_key": "door_fault"})["error"]
    assert "需要" in reg.invoke("verify_fault_action", {"expected_action": "derate"})["error"]


# ---------------------------------------------------------------------------
# 超时与权限
# ---------------------------------------------------------------------------


def test_timeout_is_real_and_reported_honestly(knowledge, tmp_path: Path) -> None:
    """超时必须由**子进程**强制执行并如实上报（不是"放弃等待"）。"""
    sandbox = ExecutionSandbox(root=tmp_path, run_id="t")
    tools = build_execution_tools(knowledge, sandbox, timeout_s=0.001)
    run_tool = next(t for t in tools if t.name == "run_scenario")
    r = run_tool.func({"scenario_file": "door_cascade.yaml"})
    assert "超时" in r["error"]
    assert r["timed_out"] is True


def test_execution_tools_hidden_and_refused_under_read_cap(knowledge, tmp_path: Path) -> None:
    """双保险：R0 档位下执行工具既不进 schema，也不可执行（在册但被拒）。"""
    reg = build_registry(knowledge, AgentConfig(max_level=Permission.READ, sandbox_dir=tmp_path))
    assert "run_scenario" not in reg.names(), "越权工具不得出现在可用清单"
    assert "run_scenario" in reg.names(allowed_only=False), "但应在册（便于如实展示）"
    assert all(
        s["function"]["name"] != "run_scenario" for s in reg.schemas()
    ), "越权工具不得出现在送给模型的 schema 里"
    r = reg.invoke("run_scenario", {"scenario_file": "door_cascade.yaml"})
    assert "权限不足" in r["error"], f"必须由 invoke 第二道防线拒绝，实际: {r}"
    assert reg.audit[-1].ok is False


def test_execution_tools_are_execute_level(knowledge, tmp_path: Path) -> None:
    sandbox = ExecutionSandbox(root=tmp_path, run_id="t")
    levels = {t.level for t in build_execution_tools(knowledge, sandbox)}
    assert levels == {Permission.EXECUTE}
