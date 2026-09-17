"""故障生命周期台账 + 故障场景 DSL 测试（faultlife.py）。"""

import pytest

from tcms import faultlevel, faultlife, recorder, timebase


def _vclock():
    return timebase.VirtualClock(mode="virtual")


# ---- FaultLifecycle 单故障 ----


def test_lifecycle_full_sequence():
    clock = _vclock()
    fl = faultlife.FaultLifecycle("overspeed", clock=clock, level=faultlevel.LEVEL_MAJOR)
    assert fl.current_stage is None
    fl.inject()
    assert fl.current_stage == faultlife.STAGE_INJECTED
    clock.advance(0.5)
    fl.propagate("traction derate")
    assert fl.current_stage == faultlife.STAGE_PROPAGATED
    clock.advance(0.5)
    fl.alert("司机提示：限速")
    assert fl.current_stage == faultlife.STAGE_ALERTED
    clock.advance(1.0)
    fl.recover()
    assert fl.current_stage == faultlife.STAGE_RECOVERED
    clock.advance(0.5)
    fl.close()
    assert fl.is_closed
    assert fl.current_stage == faultlife.STAGE_CLOSED
    # 阶段时间戳递增
    ts = [s["ts"] for s in fl.stages]
    assert ts == sorted(ts)
    assert len(ts) == 5


def test_lifecycle_impact_recorded():
    clock = _vclock()
    fl = faultlife.FaultLifecycle("temp_high", clock=clock)
    fl.inject()
    fl.propagate("cooling derate")
    fl.propagate("speed limit 80")
    assert fl.to_dict()["impact"] == ["cooling derate", "speed limit 80"]


def test_lifecycle_close_twice_rejected():
    clock = _vclock()
    fl = faultlife.FaultLifecycle("x", clock=clock)
    fl.inject()
    fl.close()
    with pytest.raises(ValueError):
        fl.close()


def test_lifecycle_invalid_stage():
    fl = faultlife.FaultLifecycle("x", clock=_vclock())
    with pytest.raises(ValueError):
        fl._mark("bogus")


def test_lifecycle_to_dict_json_serializable():
    fl = faultlife.FaultLifecycle("soc_low", clock=_vclock())
    fl.inject()
    fl.recover()
    fl.close()
    json_text = __import__("json").dumps(fl.to_dict())
    assert "overspeed" not in json_text  # 仅验证可序列化


# ---- FaultLedger 多故障台账 ----


def test_ledger_open_propagate_alert_recover_close():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    led.open("door_fault", level=faultlevel.LEVEL_MAJOR, source="bogie1")
    led.propagate("door_fault", impact="door treated as not closed")
    led.alert("door_fault", "司机告警")
    led.recover("door_fault")
    led.close("door_fault")
    report = led.report()
    assert report["total"] == 1
    assert report["closed"] == 1
    assert report["open"] == 0
    fl = led.faults["door_fault"]
    assert fl.to_dict()["source"] == "bogie1"


def test_ledger_open_faults_list():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    led.open("a", level=faultlevel.LEVEL_MINOR)
    led.open("b", level=faultlevel.LEVEL_MAJOR)
    led.close("b")
    assert led.open_faults == ["a"]
    assert led.query(open_only=True) == [led.faults["a"].to_dict()]


def test_ledger_operate_on_unknown_fault_raises():
    led = faultlife.FaultLedger(clock=_vclock())
    with pytest.raises(KeyError):
        led.recover("nope")
    with pytest.raises(KeyError):
        led.close("nope")


def test_ledger_operate_on_closed_fault_raises():
    led = faultlife.FaultLedger(clock=_vclock())
    led.open("x")
    led.close("x")
    with pytest.raises(ValueError):
        led.alert("x", "too late")


def test_ledger_open_idempotent():
    led = faultlife.FaultLedger(clock=_vclock())
    led.open("y")
    fl1 = led.faults["y"]
    led.open("y")  # 同名未归档 → 复用
    assert led.faults["y"] is fl1


def test_ledger_query_by_stage():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    led.open("p", level=faultlevel.LEVEL_CRITICAL)
    led.propagate("p", "eb")
    led.open("q", level=faultlevel.LEVEL_INFO)
    q = led.query(stage=faultlife.STAGE_PROPAGATED)
    assert [d["name"] for d in q] == ["p"]


def test_ledger_writes_to_event_recorder():
    clock = _vclock()
    rec = recorder.EventRecorder()
    led = faultlife.FaultLedger(clock=clock, event_recorder=rec)
    led.open("eb_failure", level=faultlevel.LEVEL_CRITICAL)
    led.close("eb_failure")
    events = rec.query(category="fault_lifecycle")
    messages = {e["message"] for e in events}
    assert {"open", "close"} <= messages
    assert events[0]["payload"]["fault"] == "eb_failure"


def test_ledger_report_by_level():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    led.open("minor1", level=faultlevel.LEVEL_MINOR)
    led.open("major1", level=faultlevel.LEVEL_MAJOR)
    led.open("major2", level=faultlevel.LEVEL_MAJOR)
    r = led.report()
    assert r["by_level"] == {faultlevel.LEVEL_MINOR: 1, faultlevel.LEVEL_MAJOR: 2}


# ---- FaultScenario DSL ----


def test_scenario_steps_and_duration():
    s = faultlife.FaultScenario(name="demo")
    s.when("vcu", "overspeed", at=10.0, expect="emergency_brake")
    s.expect_clear("overspeed", at=20.0)
    assert len(s.steps) == 2
    assert s.duration == 20.0
    assert s.steps[0]["node"] == "vcu"


def test_scenario_empty_duration_zero():
    assert faultlife.FaultScenario().duration == 0.0


def test_runner_executes_and_asserts():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    s = faultlife.FaultScenario(name="overspeed_scenario")
    # overspeed → major → 处置 derate（auto 模式）
    s.when("vcu", "overspeed", at=10.0, expect=faultlevel.ACTION_DERATE)
    s.expect_clear("overspeed", at=20.0)
    report = faultlife.ScenarioRunner(led, s, clock=clock).run()
    assert report["steps"] == 2
    assert report["all_passed"] is True
    assert report["assertions"][0]["expected"] == faultlevel.ACTION_DERATE
    assert report["assertions"][0]["actual"] == faultlevel.ACTION_DERATE
    # 恢复后台账关闭
    assert led.report()["open"] == 0


def test_runner_expect_mismatch_fails():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    s = faultlife.FaultScenario()
    # 期望与实际不符（overspeed 实际是 derate，期望 emergency_brake）
    s.when("vcu", "overspeed", at=5.0, expect=faultlevel.ACTION_EB)
    report = faultlife.ScenarioRunner(led, s, clock=clock).run()
    assert report["all_passed"] is False
    assert report["failed"] == 1
    assert report["assertions"][0]["passed"] is False


def test_runner_recover_unknown_fault_reports_failure():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    s = faultlife.FaultScenario()
    s.expect_clear("ghost", at=3.0)
    report = faultlife.ScenarioRunner(led, s, clock=clock).run()
    assert report["all_passed"] is False
    assert report["assertions"][0]["actual"] == "not_open"


def test_runner_steps_executed_in_ts_order():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    s = faultlife.FaultScenario()
    s.when("vcu", "soc_low", at=30.0)  # 乱序定义
    s.when("vcu", "temp_high", at=10.0)
    faultlife.ScenarioRunner(led, s, clock=clock).run()
    # 时间升序执行：temp_high 先开账
    assert list(led.faults) == ["temp_high", "soc_low"]
    assert clock.now() == 30.0  # 结束时刻 = 最后一步


@pytest.mark.safety
def test_runner_critical_fault_maps_to_eb():
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    s = faultlife.FaultScenario()
    s.when("vcu", "eb_failure", at=1.0, expect=faultlevel.ACTION_EB)
    report = faultlife.ScenarioRunner(led, s, clock=clock).run()
    assert report["all_passed"] is True
    assert report["assertions"][0]["actual"] == faultlevel.ACTION_EB


def test_runner_unknown_fault_records_failure_not_crash():
    """未知故障注入：不中断整个场景，记录 failed 断言（与 recover 兜底对称）。"""
    clock = _vclock()
    led = faultlife.FaultLedger(clock=clock)
    s = faultlife.FaultScenario()
    s.when("vcu", "ghost_fault", at=1.0, expect="derate")
    s.when("vcu", "overspeed", at=2.0, expect=faultlevel.ACTION_DERATE)
    s.expect_clear("overspeed", at=4.0)
    report = faultlife.ScenarioRunner(led, s, clock=clock).run()
    assert report["all_passed"] is False
    # 未知故障记为 failed，后续已知故障仍正常执行
    first = report["assertions"][0]
    assert first["fault"] == "ghost_fault"
    assert first["passed"] is False
    assert first["actual"].startswith("unknown:")
    assert any(a["fault"] == "overspeed" and a["passed"] for a in report["assertions"])
