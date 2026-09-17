"""R0 只读工具：返回的必须是真实资产数据（不是占位、不是编造）。"""

from __future__ import annotations

from tcms_agent.config import AgentConfig
from tcms_agent.graph import build_registry
from tcms_agent.permissions import Permission


def test_all_tools_are_readonly(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    assert len(reg) >= 7, "R0 只读工具应有 7 个（复用 5 + 原生 2）"
    levels = {t["level"] for t in reg.describe()}
    assert levels == {int(Permission.READ)}, "M1 的工具面必须全部是 R0 只读"
    assert all(t["allowed"] for t in reg.describe())


def test_expected_tool_names_present(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    names = set(reg.names())
    for expected in (
        "kb_search",
        "kb_filter_assets",
        "symptom_diagnose",
        "kb_node",
        "list_scenarios",
        "fault_detail",
        "list_requirements",
    ):
        assert expected in names, f"缺少工具 {expected}"


def test_fault_detail_returns_real_fmea_semantics(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    r = reg.invoke("fault_detail", {"fault_key": "door_fault"})
    fd = knowledge.model.faults_by_key["door_fault"]
    assert r["key"] == "door_fault"
    assert r["action"] == fd.action
    assert r["level"] == fd.level
    assert r["source"] == "faults.yaml"
    # 处置语义字段必须来自字典，不能是空壳
    assert r["detect"], "detect 字段应来自 faults.yaml"
    assert r["covering_scenario_count"] >= 1
    assert all(s["file"] for s in r["covering_scenarios"])


def test_fault_detail_rejects_unknown_key_with_guidance(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    r = reg.invoke("fault_detail", {"fault_key": "definitely_not_a_fault"})
    assert "不存在" in r["error"]
    assert "hint" in r, "失败时必须给出可用引导，而不是死路"


def test_fault_detail_empty_key_is_honest_error(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    r = reg.invoke("fault_detail", {"fault_key": "  "})
    assert "非空" in r["error"]


def test_list_requirements_returns_real_rows(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    r = reg.invoke("list_requirements", {"limit": 5})
    assert r["source"] == "tests/rtm.csv"
    assert r["total"] >= 1, "RTM 应能读出真实需求行"
    assert r["returned"] == min(5, r["total"])


def test_list_requirements_keyword_filters(knowledge) -> None:
    reg = build_registry(knowledge, AgentConfig())
    all_rows = reg.invoke("list_requirements", {"limit": 60})
    some_key = str((all_rows["items"][0] or {}).get("id") or "")
    if some_key:
        filtered = reg.invoke("list_requirements", {"keyword": some_key, "limit": 60})
        assert filtered["total"] <= all_rows["total"]


def test_kb_search_tool_returns_real_doc_ids(knowledge) -> None:
    """复用 platform 工具面：命中必须是知识底座里真实存在的文档 id。"""
    reg = build_registry(knowledge, AgentConfig())
    r = reg.invoke("kb_search", {"query": "车门故障 不能发车"})
    assert r["hits"], "应命中真实资产"
    from tcms_agent.nodes import check_reference

    for h in r["hits"]:
        assert check_reference(str(h["doc_id"]), knowledge)[0] is True, (
            f"检索命中的 doc_id 必须能在图谱/资产中核实: {h['doc_id']}"
        )
