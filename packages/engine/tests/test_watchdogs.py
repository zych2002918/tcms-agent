"""节点健康监视测试：看门狗状态迁移（含迟滞）、健康表、总线负载率。"""

import time

import pytest

from tcms import protocol as proto
from tcms.parser import count_frames
from tcms.watchdogs import (
    STATE_FAULT,
    STATE_OFFLINE,
    STATE_ONLINE,
    NodeHealthTable,
    NodeWatchdog,
)


class FakeClock:
    """可控时钟：测试无需真实等待。"""

    def __init__(self, start=0.0):
        self.t = start

    def advance(self, dt):
        self.t += dt

    def __call__(self):
        return self.t


# ---- 看门狗（fake clock，确定性） ----


def test_watchdog_starts_offline():
    clock = FakeClock()
    wd = NodeWatchdog(cycle_time=0.1, now=clock)
    assert wd.evaluate() == STATE_OFFLINE


def test_watchdog_online_after_two_feeds():
    clock = FakeClock()
    wd = NodeWatchdog(cycle_time=0.1, recover_threshold=2, now=clock)
    wd.feed()
    clock.advance(0.05)
    wd.feed()
    assert wd.evaluate() == STATE_ONLINE


def test_watchdog_single_feed_not_enough():
    clock = FakeClock()
    wd = NodeWatchdog(cycle_time=0.1, recover_threshold=2, now=clock)
    wd.feed()
    assert wd.evaluate() == STATE_OFFLINE


@pytest.mark.smoke
@pytest.mark.safety
def test_watchdog_fault_after_missed_cycles():
    clock = FakeClock()
    wd = NodeWatchdog(cycle_time=0.1, miss_threshold=3, now=clock)
    wd.feed()
    clock.advance(0.05)
    wd.feed()
    assert wd.evaluate() == STATE_ONLINE
    # 连续 3 个周期以上无心跳
    clock.advance(0.35)  # 距最后心跳 0.4s > 0.3s
    assert wd.evaluate() == STATE_FAULT


def test_watchdog_fault_has_hysteresis():
    """迟滞：fault 后单次心跳不能立即恢复（需连续 recover_threshold 次）。"""
    clock = FakeClock()
    wd = NodeWatchdog(cycle_time=0.1, miss_threshold=3, recover_threshold=2, now=clock)
    wd.feed()
    clock.advance(0.05)
    wd.feed()
    assert wd.evaluate() == STATE_ONLINE
    clock.advance(0.5)
    assert wd.evaluate() == STATE_FAULT
    # 1 次心跳 → 仍 fault
    clock.advance(0.1)
    wd.feed()
    assert wd.evaluate() == STATE_FAULT
    # 第 2 次心跳 → 恢复 online
    clock.advance(0.1)
    wd.feed()
    assert wd.evaluate() == STATE_ONLINE


def test_watchdog_steady_heartbeat_stays_online():
    clock = FakeClock()
    wd = NodeWatchdog(cycle_time=0.1, miss_threshold=3, recover_threshold=2, now=clock)
    wd.feed()
    clock.advance(0.09)
    wd.feed()  # 2 次连续心跳先上线
    for _ in range(20):
        assert wd.evaluate() == STATE_ONLINE
        clock.advance(0.09)  # 每 90ms 一跳（正常）
        wd.feed()


# ---- 健康表 ----


def test_health_table_tracks_multiple_nodes():
    clock = FakeClock()
    table = NodeHealthTable(cycle_time=0.1, miss_threshold=3, now=clock)
    table.feed("VCU")
    clock.advance(0.05)
    table.feed("VCU")
    table.feed("BMS")
    clock.advance(0.05)
    table.feed("BMS")
    states = table.evaluate()
    assert states["VCU"] == STATE_ONLINE
    assert states["BMS"] == STATE_ONLINE
    # VCU 停发
    clock.advance(0.5)
    states = table.evaluate()
    assert states["VCU"] == STATE_FAULT
    assert states["BMS"] == STATE_FAULT  # BMS 也超时


def test_health_table_unknown_node_status():
    table = NodeHealthTable(cycle_time=0.1, miss_threshold=3)
    assert table.status("GHOST") == STATE_OFFLINE


# ---- 看门狗 × 真实仿真器（集成） ----


def test_watchdog_integration_with_simulator(bus, db, simulator):
    """仿真器丢报后，看门狗在 3 个周期内判 Fault。"""
    table = NodeHealthTable(cycle_time=0.1, miss_threshold=3)
    # 收集 0.5s 心跳喂狗
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        msg = bus.recv(timeout=0.05)
        if msg is not None and msg.arbitration_id == proto.TCMS_HEARTBEAT:
            table.feed("VCU")
    table.evaluate()
    assert table.status("VCU") == STATE_ONLINE
    # 停止心跳（丢报注入）
    simulator.stop_message(proto.TCMS_HEARTBEAT)
    time.sleep(0.5)  # 超过 3×100ms
    table.evaluate()  # 评估必须显式调用
    assert table.status("VCU") == STATE_FAULT


# ---- 总线负载率 ----


def test_bus_load_rate_within_budget(bus, db, simulator):
    """正常工况下总线负载率应远低于预算（如 500kbps 下 < 20%）。"""
    duration = 1.0
    n_heartbeat = count_frames(bus, duration, proto.TCMS_HEARTBEAT)
    n_handle = count_frames(bus, duration, proto.TRACTION_BRAKE_HANDLE)
    # 估算总帧数：心跳 10 帧/s、手柄 20 帧/s，每帧约 130 bit（含填充/IFS）
    total_bits = (n_heartbeat * 130) + (n_handle * 130)
    load_percent = total_bits / (500_000 * duration) * 100
    assert load_percent < 20, f"总线负载率 {load_percent:.1f}% 超出预算"


def test_bus_load_rate_single_message():
    """负载率计算：帧数/时长/比特率换算。"""
    frames = 10
    duration = 1.0
    load = (frames * 130) / (500_000 * duration) * 100
    assert load == pytest.approx(0.26)
