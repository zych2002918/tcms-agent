"""A3 场景 YAML：声明式故障场景外部化测试。

覆盖：YAML 解析（两种写法）、加载、批量执行、与 FaultLedger/ScenarioRunner
打通、错误处理（缺 at/未知动作/空 YAML/缺目录）、虚拟时钟驱动。
"""

import textwrap

import pytest

from tcms import scenarios
from tcms.faultlife import FaultLedger
from tcms.timebase import VirtualClock

SAMPLE = textwrap.dedent("""\
    name: 超速降级
    steps:
      - at: 10.0
        inject:
          node: vcu
          fault: overspeed
          level: major
          impact: 速度超限
          expect: derate
      - at: 20.0
        recover: overspeed
""")


def test_parse_scenario_basic():
    sc = scenarios.parse_scenario(SAMPLE, name="demo")
    assert sc.name == "demo"
    assert len(sc.steps) == 2
    inj = sc.steps[0]
    assert inj["action"] == "inject"
    assert inj["fault"] == "overspeed"
    assert inj["ts"] == pytest.approx(10.0)
    assert inj["expect"] == "derate"
    assert sc.steps[1] == {"ts": 20.0, "action": "recover", "fault": "overspeed"}


def test_parse_scenario_event_style():
    """事件式写法：action 字段 + 顶层 fault 字段。"""
    text = textwrap.dedent("""\
        name: 事件式
        steps:
          - at: 1.0
            action: inject
            node: bcu
            fault: door_fault
            level: major
            expect: derate
          - at: 2.0
            action: recover
            fault: door_fault
    """)
    sc = scenarios.parse_scenario(text)
    assert sc.steps[0]["action"] == "inject"
    assert sc.steps[0]["fault"] == "door_fault"
    assert sc.steps[1]["action"] == "recover"


def test_parse_scenario_default_level():
    """未指定 level 时默认 major。"""
    text = textwrap.dedent("""\
        steps:
          - at: 1.0
            inject:
              node: vcu
              fault: overspeed
    """)
    sc = scenarios.parse_scenario(text)
    assert sc.steps[0]["level"] == "major"


def test_parse_scenario_missing_at_rejected():
    with pytest.raises(ValueError, match="at"):
        scenarios.parse_scenario("steps:\n  - inject:\n      fault: x\n")


def test_parse_scenario_unknown_action_rejected():
    with pytest.raises(ValueError, match="无法识别"):
        scenarios.parse_scenario("steps:\n  - at: 1.0\n    action: explode\n    fault: x\n")


def test_parse_scenario_empty_rejected():
    with pytest.raises(ValueError, match="空"):
        scenarios.parse_scenario("")


def test_parse_scenario_no_steps_rejected():
    with pytest.raises(ValueError, match="steps"):
        scenarios.parse_scenario("name: 无步骤\n")


def test_run_yaml_virtual_clock():
    """YAML 场景在虚拟时钟下执行，expect 断言生效。"""
    clock = VirtualClock(mode="virtual")
    ledger = FaultLedger(clock)
    sc = scenarios.parse_scenario(SAMPLE, name="demo")
    from tcms.faultlife import ScenarioRunner

    report = ScenarioRunner(ledger, sc, clock).run()
    assert report["all_passed"] is True
    assert report["assertions"][0]["expected"] == "derate"
    assert report["assertions"][0]["actual"] == "derate"
    assert report["passed"] == 1
    assert clock.now() == pytest.approx(20.0)  # 虚拟时钟推进到最后一步


def test_run_yaml_expect_mismatch_fails():
    """期望处置与实际不符时 passed=False。"""
    text = textwrap.dedent("""\
        steps:
          - at: 1.0
            inject:
              node: vcu
              fault: overspeed
              expect: emergency_brake
    """)
    clock = VirtualClock(mode="virtual")
    ledger = FaultLedger(clock)
    from tcms.faultlife import ScenarioRunner

    report = ScenarioRunner(ledger, scenarios.parse_scenario(text), clock).run()
    assert report["all_passed"] is False
    assert report["assertions"][0]["passed"] is False


def test_load_scenario_file(tmp_path):
    p = tmp_path / "demo.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    sc = scenarios.load_scenario(p)
    assert sc.name == "demo"
    assert len(sc.steps) == 2


def test_load_scenarios_dir(tmp_path):
    (tmp_path / "a.yaml").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(SAMPLE.replace("超速降级", "场景B"), encoding="utf-8")
    scs = scenarios.load_scenarios(tmp_path)
    assert len(scs) == 2
    assert [s.name for s in scs] == ["a", "b"]  # 文件名排序


def test_load_scenarios_missing_dir():
    with pytest.raises(FileNotFoundError):
        scenarios.load_scenarios("nonexistent_dir_xyz")


def test_run_yaml_one_shot(tmp_path):
    """run_yaml 一键：加载 + 执行 + 报告。"""
    p = tmp_path / "demo.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    clock = VirtualClock(mode="virtual")
    report = scenarios.run_yaml(p, clock=clock)
    assert report["all_passed"] is True


def test_run_yaml_default_clock(tmp_path):
    """run_yaml 缺省时钟：自动创建 virtual 时钟，一键可跑（无需显式时钟）。"""
    p = tmp_path / "demo.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    report = scenarios.run_yaml(p)
    assert report["all_passed"] is True
    assert report["ledger"]["total"] == 1


def test_run_scenarios_default_clock(tmp_path):
    """run_scenarios 缺省时钟：批量一键可跑。"""
    (tmp_path / "a.yaml").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(SAMPLE, encoding="utf-8")
    reports = scenarios.run_scenarios(tmp_path)
    assert len(reports) == 2
    assert all(r["all_passed"] for r in reports)


def test_run_scenarios_batch(tmp_path):
    """批量执行：多场景独立台账，全部通过。"""
    (tmp_path / "a.yaml").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(
        textwrap.dedent("""\
        steps:
          - at: 1.0
            inject:
              node: vcu
              fault: door_fault
              expect: derate
    """),
        encoding="utf-8",
    )
    clock = VirtualClock(mode="virtual")
    reports = scenarios.run_scenarios(tmp_path, clock=clock)
    assert len(reports) == 2
    assert all(r["all_passed"] for r in reports)


def test_run_scenarios_isolated_ledger(tmp_path):
    """批量执行时台账相互隔离（不串账）。"""
    (tmp_path / "a.yaml").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(SAMPLE, encoding="utf-8")
    clock = VirtualClock(mode="virtual")
    reports = scenarios.run_scenarios(tmp_path, clock=clock)
    for r in reports:
        assert r["ledger"]["total"] == 1  # 每个场景只开 1 个故障
