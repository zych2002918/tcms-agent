"""节点生命周期状态机测试：合法迁移、非法迁移拒绝、编码值、仿真器集成。"""

import pytest

from tcms import protocol as proto
from tcms.lifecycle import (
    ACTIVE,
    FAULT,
    POWER_OFF,
    STANDBY,
    STATUS_CODE,
    NodeLifecycle,
)
from tcms.parser import collect


def test_initial_state_power_off():
    lc = NodeLifecycle("VCU")
    assert lc.state == POWER_OFF
    assert lc.status_code == 0


def test_full_lifecycle_sequence():
    """完整生命周期：上电→就绪→运行→故障→复位→再运行。"""
    lc = NodeLifecycle("VCU")
    lc.power_on()
    assert lc.state == STANDBY
    lc.ready()
    assert lc.state == ACTIVE
    lc.fail()
    assert lc.state == FAULT
    lc.reset()
    assert lc.state == STANDBY
    lc.ready()
    assert lc.state == ACTIVE


def test_power_off_after_standby():
    lc = NodeLifecycle("VCU")
    lc.power_on()
    lc.power_off()
    assert lc.state == POWER_OFF


def test_illegal_transition_rejected():
    """非法迁移（如 PowerOff 直接 Active、Standby 直接 Fault）必须拒绝。"""
    lc = NodeLifecycle("VCU")
    with pytest.raises(ValueError):
        lc.transition(ACTIVE)
    lc.power_on()
    with pytest.raises(ValueError):
        lc.fail()  # Standby 不能直接进 Fault
    lc.power_off()  # Standby 下电是合法迁移
    assert lc.state == POWER_OFF


def test_fault_requires_reset():
    """故障后必须先复位，不能直接回 Active。"""
    lc = NodeLifecycle("VCU")
    lc.power_on()
    lc.ready()
    lc.fail()
    with pytest.raises(ValueError):
        lc.ready()
    lc.reset()
    lc.ready()  # 复位后可再次就绪


def test_status_code_mapping():
    assert STATUS_CODE == {POWER_OFF: 0, STANDBY: 1, ACTIVE: 2, FAULT: 3}


def test_heartbeat_carries_node_status(bus, db, simulator):
    """仿真器心跳应携带 NodeStatus=Active（编码值 2）。"""
    collected = collect(bus, 0.4, {proto.TCMS_HEARTBEAT}, db)
    frames = collected[proto.TCMS_HEARTBEAT]
    assert frames
    assert all(f["NodeStatus"] == "Active" for f in frames)
