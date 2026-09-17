"""总线级故障注入测试：短路/断路→集体 Bus-Off→恢复；干扰→REC 上升。"""

import pytest

from tcms import busfault as bf
from tcms import errstate


def make_injector():
    bfi = bf.BusFaultInjector()
    for name in ("VCU", "BCU", "BMS"):
        bfi.add_node(name)
    return bfi


# ---- 短路 ----


def test_short_all_nodes_bus_off():
    bfi = make_injector()
    bfi.inject(bf.FAULT_SHORT)
    assert bfi.active_fault == bf.FAULT_SHORT
    assert len(bfi.bus_off_nodes()) == 3  # 全体 Bus-Off
    assert bfi.status_report()["nodes"]["VCU"] == errstate.STATE_BUS_OFF


def test_short_recover_restores():
    bfi = make_injector()
    bfi.inject(bf.FAULT_SHORT)
    bfi.recover()
    assert bfi.active_fault is None
    assert bfi.bus_off_nodes() == []
    assert bfi.status_report()["nodes"]["VCU"] == errstate.STATE_ERROR_ACTIVE


def test_short_requires_recovery_before_reinject():
    bfi = make_injector()
    bfi.inject(bf.FAULT_SHORT)
    with pytest.raises(RuntimeError):
        bfi.inject(bf.FAULT_OPEN)


# ---- 断路 ----


def test_open_all_nodes_bus_off():
    bfi = make_injector()
    bfi.inject(bf.FAULT_OPEN)
    assert len(bfi.bus_off_nodes()) == 3


def test_open_recover():
    bfi = make_injector()
    bfi.inject(bf.FAULT_OPEN)
    bfi.recover()
    assert bfi.bus_off_nodes() == []


# ---- 干扰 ----


def test_interference_rec_only_no_bus_off():
    bfi = make_injector()
    bfi.inject(bf.FAULT_INTERFERENCE)
    assert bfi.active_fault == bf.FAULT_INTERFERENCE
    assert bfi.bus_off_nodes() == []  # 干扰不触发 Bus-Off
    # REC 上升（接收错误 8 次 × 8 = 64），节点仍 active/passive 但非 bus-off
    for name in bfi.node_names:
        sm = bfi._nodes[name]
        assert sm.rec == 64
        assert sm.state != errstate.STATE_BUS_OFF


def test_interference_recover_clears_fault():
    bfi = make_injector()
    bfi.inject(bf.FAULT_INTERFERENCE)
    bfi.recover()
    assert bfi.active_fault is None


# ---- 边界 ----


def test_invalid_fault_type():
    bfi = make_injector()
    with pytest.raises(ValueError):
        bfi.inject("lightning")


def test_duplicate_node_rejected():
    bfi = make_injector()
    with pytest.raises(ValueError):
        bfi.add_node("VCU")


def test_empty_injector_noop():
    bfi = bf.BusFaultInjector()
    bfi.inject(bf.FAULT_SHORT)  # 无节点：不崩溃
    assert bfi.bus_off_nodes() == []
    bfi.recover()


def test_partial_recovery_keeps_fault_state():
    """恢复未达 128 空闲位时保持 Bus-Off（部分恢复）。"""
    bfi = make_injector()
    bfi.inject(bf.FAULT_SHORT)
    # 只给 64 次空闲位：仍 Bus-Off
    bfi._nodes["VCU"].bus_idle_bit(64)
    assert bfi._nodes["VCU"].state == errstate.STATE_BUS_OFF
    # 再补 64 次：恢复
    bfi._nodes["VCU"].bus_idle_bit(64)
    assert bfi._nodes["VCU"].state == errstate.STATE_ERROR_ACTIVE
