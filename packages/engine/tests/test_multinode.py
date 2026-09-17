"""多节点总线仿真测试：节点报文归属、节点失活隔离、节点恢复。"""

import time

from tcms import protocol as proto
from tcms.multinode import NODE_MESSAGES, MultiNodeSimulator
from tcms.parser import count_frames


def _assert_node_online(bus, node):
    """断言节点所有周期报文都出现在总线上。"""
    for mid in NODE_MESSAGES[node]:
        if mid == proto.ALARM_EVENT:
            continue
        assert count_frames(bus, 0.7, mid) >= 1, f"{node} 报文 0x{mid:X} 缺失"


def test_all_nodes_started(bus, db):
    """全部节点启动后，各节点报文均出现在总线上。"""
    sim = MultiNodeSimulator(bus, db)
    sim.start()
    try:
        time.sleep(0.8)
        assert set(sim.active_nodes) == {"VCU", "BCU", "BMS"}
        for node in ("VCU", "BCU", "BMS"):
            _assert_node_online(bus, node)
    finally:
        sim.stop()


def test_node_ownership_mapping():
    """节点-报文归属映射应覆盖全部 7 个周期报文，且不重复。"""
    owned = [mid for mids in NODE_MESSAGES.values() for mid in mids]
    assert len(owned) == len(set(owned)), "报文归属存在重复"
    assert set(owned) == {
        proto.TCMS_HEARTBEAT,
        proto.VEHICLE_SPEED,
        proto.TRACTION_BRAKE_HANDLE,
        proto.DOOR_CONTROL,
        proto.PANTOGRAPH_STATUS,
        proto.BRAKE_SYSTEM,
        proto.ENERGY_STATUS,
        proto.ALARM_EVENT,
    }


def test_disable_bms_isolates_energy_only(bus, db):
    """BMS 失活：仅能源报文消失，其余节点报文不受影响。"""
    sim = MultiNodeSimulator(bus, db)
    sim.start()
    try:
        time.sleep(0.5)
        sim.disable_node("BMS")
        time.sleep(0.6)
        while bus.recv(timeout=0.01) is not None:
            pass
        assert count_frames(bus, 0.7, proto.ENERGY_STATUS) == 0
        assert count_frames(bus, 0.7, proto.TCMS_HEARTBEAT) >= 5
        assert count_frames(bus, 0.7, proto.BRAKE_SYSTEM) >= 5
        assert sim.active_nodes == ["VCU", "BCU"]
    finally:
        sim.stop()


def test_disable_bcu_isolates_brake_and_pantograph(bus, db):
    """BCU 失活：受电弓/制动报文消失，VCU/BMS 正常。"""
    sim = MultiNodeSimulator(bus, db)
    sim.start()
    try:
        time.sleep(0.5)
        sim.disable_node("BCU")
        time.sleep(0.7)
        while bus.recv(timeout=0.01) is not None:
            pass
        assert count_frames(bus, 0.7, proto.BRAKE_SYSTEM) == 0
        assert count_frames(bus, 0.7, proto.PANTOGRAPH_STATUS) == 0
        assert count_frames(bus, 0.7, proto.VEHICLE_SPEED) >= 5
        assert count_frames(bus, 0.7, proto.ENERGY_STATUS) >= 1
    finally:
        sim.stop()


def test_enable_node_recovers(bus, db):
    """节点恢复后报文应重新出现（故障恢复场景）。"""
    sim = MultiNodeSimulator(bus, db)
    sim.start()
    try:
        time.sleep(0.5)
        sim.disable_node("BMS")
        time.sleep(0.6)
        while bus.recv(timeout=0.01) is not None:
            pass
        assert count_frames(bus, 0.6, proto.ENERGY_STATUS) == 0
        sim.enable_node("BMS")
        time.sleep(0.7)
        assert count_frames(bus, 0.7, proto.ENERGY_STATUS) >= 1
    finally:
        sim.stop()


def test_disable_unknown_node_raises(bus, db):
    """失活未知节点应抛出 ValueError。"""
    sim = MultiNodeSimulator(bus, db)
    try:
        import pytest

        with pytest.raises(ValueError):
            sim.disable_node("UNKNOWN")
    finally:
        sim.stop()
