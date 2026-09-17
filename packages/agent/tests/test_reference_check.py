"""引用强制校验：把"引用幻觉"变成机器可抓的错误。

这是本项目相对"LLM 聊天框"最本质的差别之一——**结论里的每个引用都要回真实
资产核对**，不靠人眼看、不靠模型自证。
"""

from __future__ import annotations

from tcms_agent.nodes import check_reference


def test_real_fault_reference_passes(knowledge) -> None:
    assert "door_fault" in knowledge.model.faults_by_key
    ok, via = check_reference("fault:door_fault", knowledge)
    assert ok is True
    assert via == "faults.yaml"


def test_fabricated_fault_reference_is_caught(knowledge) -> None:
    """编造的故障键必须被判定为不存在（哪怕名字看起来很合理）。"""
    ok, _ = check_reference("fault:door_fault_super_critical", knowledge)
    assert ok is False


def test_real_scenario_reference_passes_and_fake_fails(knowledge) -> None:
    some = next(iter(knowledge.model.scenarios.values()))
    assert check_reference(f"scenario:{some.file}", knowledge)[0] is True
    assert check_reference("scenario:no_such_scenario.yaml", knowledge)[0] is False


def test_requirement_reference_resolves_via_graph(knowledge) -> None:
    req_nodes = [n for n in knowledge.graph.nodes if n.startswith("requirement:")]
    assert req_nodes, "图谱应含 requirement: 节点（SR-xx 需求锚点）"
    sr = req_nodes[0].split(":", 1)[1]
    ok, via = check_reference(f"req:{sr}", knowledge)
    assert ok is True, f"需求引用应按图谱节点解析: {sr}"
    assert "图谱" in via


def test_malformed_reference_rejected(knowledge) -> None:
    ok, via = check_reference("这不是一个合法引用", knowledge)
    assert ok is False
    assert "格式非法" in via


def test_every_registered_fault_is_checkable(knowledge) -> None:
    """自证：全部真实故障键都能通过校验（防止校验器本身过严而误杀真实引用）。"""
    bad = [k for k in knowledge.model.faults_by_key if not check_reference(f"fault:{k}", knowledge)[0]]
    assert bad == [], f"校验器误杀真实故障键: {bad[:5]}"
