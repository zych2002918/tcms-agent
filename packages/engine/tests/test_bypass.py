"""隔离/旁路开关状态机测试：旁路前提、审计、隔离组聚合、降级兜底。"""

import pytest

from tcms import bypass as bp
from tcms import ebm

SW_CLOSED = bp.SW_CLOSED
SW_OPEN = bp.SW_OPEN


def make_switch(name="ATP_ISO", device="ATP"):
    return bp.IsolationSwitch(name=name, device=device)


# ---- 单开关 ----


def test_initial_closed():
    sw = make_switch()
    assert sw.state == SW_CLOSED
    assert not sw.bypassed


def test_open_switch_at_zero_speed():
    sw = make_switch()
    assert sw.open_switch(speed_kmh=0.0) is True
    assert sw.state == SW_OPEN
    assert sw.bypassed


def test_open_switch_rejects_moving():
    """运行中禁止旁路（旁路即解除安全链）。"""
    sw = make_switch()
    assert sw.open_switch(speed_kmh=10.0) is False
    assert not sw.bypassed


def test_open_switch_rejects_invalid_speed_signal():
    """速度信号失效时禁止旁路。"""
    sw = make_switch()
    assert sw.open_switch(speed_kmh=0.0, speed_valid=False) is False


def test_open_switch_idempotent():
    sw = make_switch()
    sw.open_switch()
    assert sw.open_switch() is False  # 已旁路：拒绝重复


def test_close_switch_restores():
    sw = make_switch()
    sw.open_switch()
    assert sw.close_switch() is True
    assert sw.state == SW_CLOSED
    assert not sw.bypassed


def test_close_switch_idempotent():
    sw = make_switch()
    assert sw.close_switch() is False  # 已闭合


def test_zero_speed_threshold_boundary():
    sw = bp.IsolationSwitch(name="X", device="Y", zero_speed_threshold_kmh=0.5)
    assert sw.open_switch(speed_kmh=0.5) is True  # 边界=阈值：允许
    sw.reset()
    assert sw.open_switch(speed_kmh=0.51) is False  # 超阈值：拒绝


# ---- 审计日志 ----


def test_audit_log_records_open_and_close():
    sw = make_switch()
    sw.open_switch(speed_kmh=0.0, operator="ops", reason="ATP 故障")
    sw.close_switch(operator="ops", reason="修复完成")
    log = sw.audit_log
    assert len(log) == 2
    assert log[0]["event"] == bp.BYPASS_EVENT_OPEN
    assert log[0]["operator"] == "ops"
    assert log[0]["reason"] == "ATP 故障"
    assert log[1]["event"] == bp.BYPASS_EVENT_CLOSE


def test_audit_log_is_deep_copy():
    sw = make_switch()
    sw.open_switch()
    sw.audit_log[0]["reason"] = "篡改"
    assert sw.audit_log[0]["reason"] == ""


def test_empty_switch_name_raises():
    with pytest.raises(ValueError):
        bp.IsolationSwitch(name="", device="ATP")


# ---- 隔离组聚合 ----


def test_group_no_bypass():
    g = bp.IsolationGroup([make_switch("A", "ATP"), make_switch("B", "EBR")])
    assert not g.bypassed_any
    assert g.bypassed_names == []


def test_group_any_bypass():
    g = bp.IsolationGroup([make_switch("A", "ATP"), make_switch("B", "EBR")])
    g.switches[0].open_switch()
    assert g.bypassed_any
    assert g.bypassed_names == ["A"]


def test_group_empty_raises():
    with pytest.raises(ValueError):
        bp.IsolationGroup([])


def test_check_degradation_forces_rm():
    g = bp.IsolationGroup([make_switch("A", "ATP")])
    assert g.check_degradation(ebm.MODE_FAM) == ebm.MODE_FAM  # 未旁路
    g.switches[0].open_switch()
    assert g.check_degradation(ebm.MODE_FAM) == ebm.MODE_RM  # 旁路 → RM
    assert g.check_degradation(ebm.MODE_CM) == ebm.MODE_RM


def test_can_upgrade_blocked_while_bypassed():
    g = bp.IsolationGroup([make_switch("A", "ATP")])
    assert g.can_upgrade(ebm.MODE_FAM) is True
    g.switches[0].open_switch()
    assert g.can_upgrade(ebm.MODE_FAM) is False  # 旁路中禁止升模式
    assert g.can_upgrade(ebm.MODE_RM) is True  # RM 允许
    g.switches[0].close_switch()
    assert g.can_upgrade(ebm.MODE_FAM) is True  # 恢复后允许


def test_status_report():
    g = bp.IsolationGroup([make_switch("A", "ATP")])
    r = g.status_report()
    assert r["bypassed_any"] is False
    assert r["required_mode"] is None
    g.switches[0].open_switch()
    r = g.status_report()
    assert r["bypassed_any"] is True
    assert r["bypassed"] == ["A"]
    assert r["required_mode"] == ebm.MODE_RM


# ---- 与 EBM 模式互操作（旁路 → 降级兜底闭环） ----


def test_bypass_interop_with_ebm_mode_chain():
    """旁路 ATP → 强制 RM；恢复闭合后按 EBM 降级链可升回。"""
    sw = make_switch("ATP_ISO", "ATP")
    g = bp.IsolationGroup([sw])
    mgr = ebm.EmergencyBrakeManager(mode=ebm.MODE_FAM)

    # 旁路触发
    assert sw.open_switch(speed_kmh=0.0, operator="ops", reason="ATP 故障")
    assert g.check_degradation(mgr.mode) == ebm.MODE_RM

    # 模拟系统按降级要求切换模式（EBM 单步降级链：FAM→CM→RM）
    mgr.set_mode(ebm.MODE_CM)
    mgr.set_mode(ebm.MODE_RM)
    assert mgr.mode == ebm.MODE_RM

    # 修复后闭合 → 允许升回（EBM 单步：RM→CM→FAM）
    assert sw.close_switch(operator="ops", reason="修复完成")
    assert g.can_upgrade(ebm.MODE_CM) is True
    mgr.set_mode(ebm.MODE_CM)
    mgr.set_mode(ebm.MODE_FAM)
    assert mgr.mode == ebm.MODE_FAM
