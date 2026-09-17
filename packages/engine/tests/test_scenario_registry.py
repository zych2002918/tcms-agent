"""场景注册表测试：仓库内 scenarios/*.yaml 直接作为用例参数化执行。

领域审计 P2-14 落地：数据资产必须被消费——新增/修改一个场景 YAML
即新增一个测试用例，场景中引用的故障键必须存在于统一故障字典
（tcms/faults.yaml，经 tcms/faultdb 校验），杜绝"场景引用幽灵故障"。
"""

from pathlib import Path

import pytest

from tcms import scenarios
from tcms.faultdb import load_fault_dictionary

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
DICT = load_fault_dictionary()

# 收集仓库内全部场景 YAML（作为参数化用例；无场景文件时显式跳过）
scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))


def _scenario_id(path: Path) -> str:
    return path.stem


@pytest.mark.parametrize("path", scenario_files, ids=_scenario_id)
def test_scenario_registry_runs_clean(path):
    """每个场景 YAML 必须能完整执行且全部断言通过（all_passed）。"""
    report = scenarios.run_yaml(path)
    assert report["all_passed"] is True, f"场景 {path.name} 执行失败: {report}"
    assert report["ledger"]["total"] >= 1
    assert report["failed"] == 0


@pytest.mark.parametrize("path", scenario_files, ids=_scenario_id)
def test_scenario_faults_exist_in_dictionary(path):
    """场景引用的每个故障键必须存在于统一故障字典（FMEA 闭环）。"""
    text = path.read_text(encoding="utf-8")
    import re

    # 粗查：所有 fault: xxx 引用都应在字典中
    for m in re.finditer(r"fault:\s*([A-Za-z_][A-Za-z0-9_]*)", text):
        assert m.group(1) in DICT.keys(), (
            f"场景 {path.name} 引用未知故障 {m.group(1)!r}（字典无此键）"
        )


def test_scenario_registry_nonempty():
    """注册表非空守卫：scenarios/ 必须至少有一个场景。"""
    assert scenario_files, "scenarios/ 目录为空，注册表测试失去意义"


def test_scenario_library_covers_dictionary_subset():
    """场景库必须覆盖故障字典的非平凡子集（FMEA → 场景 → 用例闭环）。

    每个场景都要真正注入故障（字典条目被消费）；若场景数 ≤1，
    说明数据资产未被有效利用——FMEA 字典只是摆设。
    """
    assert len(scenario_files) >= 5, (
        f"场景库仅 {len(scenario_files)} 个，无法覆盖故障字典的真实子集——请扩充 scenarios/*.yaml"
    )


def test_every_fault_referenced_by_a_scenario():
    """无孤儿故障：字典里每个故障键都至少被一个场景引用（注入或恢复）。

    新增故障键必须配套场景（真实域闭环）；防止"字典加了、引擎却永远
    不被演练"的静默资产。解析场景内 fault: 引用（含 inject/recover 两种写法）。
    """
    import re

    referenced: set[str] = set()
    for path in scenario_files:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"fault:\s*([A-Za-z_][A-Za-z0-9_]*)", text):
            referenced.add(m.group(1))
    orphans = set(DICT.keys()) - referenced
    assert not orphans, f"孤儿故障（无场景引用）: {sorted(orphans)}"
