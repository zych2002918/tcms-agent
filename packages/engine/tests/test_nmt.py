"""CANopen NMT 心跳层测试（CiA 301）：生产者/消费者/超时/状态迁移。"""

import pytest

from tcms import nmt as nm

NMT_BOOTUP = nm.NMT_BOOTUP
NMT_STOPPED = nm.NMT_STOPPED
NMT_OPERATIONAL = nm.NMT_OPERATIONAL
NMT_PRE_OPERATIONAL = nm.NMT_PRE_OPERATIONAL
ONLINE = nm.NODE_ONLINE
LOST = nm.NODE_HEARTBEAT_LOST


# ---- 生产者 ----


def test_producer_cob_id():
    p = nm.HeartbeatProducer(node_id=5)
    assert p.cob_id == 0x705


def test_producer_bootup_then_state():
    p = nm.HeartbeatProducer(node_id=1)
    assert p.heartbeat_payload() == bytes([NMT_BOOTUP])  # 首帧 boot-up
    assert p.heartbeat_payload() == bytes([NMT_PRE_OPERATIONAL])  # 之后当前状态


def test_producer_state_transitions():
    p = nm.HeartbeatProducer(node_id=1)
    p.set_state(NMT_OPERATIONAL)
    p.heartbeat_payload()  # 消耗 boot-up
    assert p.heartbeat_payload() == bytes([NMT_OPERATIONAL])
    p.set_state(NMT_STOPPED)
    assert p.heartbeat_payload() == bytes([NMT_STOPPED])


def test_producer_invalid_state():
    p = nm.HeartbeatProducer(node_id=1)
    with pytest.raises(ValueError):
        p.set_state(0x42)


def test_producer_reset_resent_bootup():
    p = nm.HeartbeatProducer(node_id=1)
    p.heartbeat_payload()  # boot-up
    p.reset()
    assert p.state == NMT_PRE_OPERATIONAL
    assert p.heartbeat_payload() == bytes([NMT_BOOTUP])  # 复位后重发 boot-up


def test_node_id_bounds():
    with pytest.raises(ValueError):
        nm.HeartbeatProducer(node_id=0)
    with pytest.raises(ValueError):
        nm.HeartbeatProducer(node_id=128)


# ---- 消费者 ----


def test_consumer_initial_lost():
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    assert hc.state == LOST


def test_consumer_online_after_heartbeat():
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    hc.on_heartbeat(NMT_OPERATIONAL, 0.0)
    assert hc.state == ONLINE


def test_consumer_timeout_lost():
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    hc.on_heartbeat(NMT_OPERATIONAL, 0.0)
    assert hc.check_timeout(0.29) == ONLINE  # < 0.3
    assert hc.check_timeout(0.31) == LOST  # > 0.3
    assert hc._events == 1


def test_consumer_bootup_resets_timer():
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    hc.on_heartbeat(NMT_OPERATIONAL, 0.0)
    hc.on_heartbeat(NMT_BOOTUP, 0.0)  # 节点重启：重新计时
    assert hc.state == ONLINE
    assert hc.check_timeout(0.29) == ONLINE


def test_consumer_reset():
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    hc.on_heartbeat(NMT_OPERATIONAL, 0.0)
    hc.reset()
    assert hc.state == LOST
    assert hc._events == 0


def test_consumer_never_received_no_timeout_event():
    """从未收到心跳时 check_timeout 不产生事件（首次上线前的静默）。"""
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    assert hc.check_timeout(10.0) == LOST
    assert hc._events == 0


# ---- 集成：生产者→消费者 ----


def test_producer_consumer_loop():
    p = nm.HeartbeatProducer(node_id=2)
    hc = nm.HeartbeatConsumer(period_ms=100, timeout_ms=300)
    ts = 0.0
    hc.on_heartbeat(p.heartbeat_payload(), ts)  # boot-up
    for i in range(5):
        ts += 0.1
        hc.on_heartbeat(p.heartbeat_payload(), ts)
        assert hc.check_timeout(ts) == ONLINE
    # 停止心跳 → 超时丢失
    ts += 0.35
    assert hc.check_timeout(ts) == LOST


# ---- NMT 主站命令（CiA 301） ----


def test_master_command_frame_format():
    """命令帧 = (COB-ID 0x000, [命令, node_id])。"""
    master = nm.NmtMaster()
    cob, payload = master.command_frame(nm.NMT_CMD_START, node_id=5)
    assert cob == nm.NMT_COB_ID
    assert payload == bytes([nm.NMT_CMD_START, 5])


def test_master_broadcast_node_zero():
    cob, payload = master_broadcast()
    assert cob == nm.NMT_COB_ID
    assert payload == bytes([nm.NMT_CMD_STOP, 0])  # 广播 node_id=0


def master_broadcast():
    m = nm.NmtMaster()
    return m.command_frame(nm.NMT_CMD_STOP, node_id=0)


def test_master_invalid_command_raises():
    m = nm.NmtMaster()
    with pytest.raises(ValueError):
        m.command_frame(0x99, node_id=1)


def test_master_invalid_node_id_raises():
    m = nm.NmtMaster()
    with pytest.raises(ValueError):
        m.command_frame(nm.NMT_CMD_START, node_id=128)
    with pytest.raises(ValueError):
        m.command_frame(nm.NMT_CMD_START, node_id=-1)


def test_master_apply_start_to_producer():
    """Start 命令 → 从站进入 Operational。"""
    m = nm.NmtMaster()
    p = nm.HeartbeatProducer(node_id=3)
    assert p.state == NMT_PRE_OPERATIONAL
    state = m.apply_to_producer(nm.NMT_CMD_START, p)
    assert state == NMT_OPERATIONAL
    assert p.state == NMT_OPERATIONAL
    p.heartbeat_payload()  # 消耗 boot-up
    assert p.heartbeat_payload() == bytes([NMT_OPERATIONAL])


def test_master_apply_stop_and_preop():
    m = nm.NmtMaster()
    p = nm.HeartbeatProducer(node_id=3)
    m.apply_to_producer(nm.NMT_CMD_START, p)
    assert m.apply_to_producer(nm.NMT_CMD_STOP, p) == NMT_STOPPED
    assert m.apply_to_producer(nm.NMT_CMD_ENTER_PRE_OPERATIONAL, p) == NMT_PRE_OPERATIONAL


def test_master_apply_reset_node_resent_bootup():
    """Reset-node → 回到 Pre-operational 且重发 boot-up（对标真实重启）。"""
    m = nm.NmtMaster()
    p = nm.HeartbeatProducer(node_id=3)
    m.apply_to_producer(nm.NMT_CMD_START, p)
    p.heartbeat_payload()
    assert m.apply_to_producer(nm.NMT_CMD_RESET_NODE, p) == NMT_PRE_OPERATIONAL
    assert p.heartbeat_payload() == bytes([NMT_BOOTUP])  # 重启后首帧 boot-up


def test_master_command_log_audit():
    m = nm.NmtMaster()
    m.command_frame(nm.NMT_CMD_START, node_id=4)
    m.command_frame(nm.NMT_CMD_STOP, node_id=4)
    log = m.command_log
    assert len(log) == 2
    assert log[0]["command"] == nm.NMT_CMD_START
    assert log[1]["node_id"] == 4
    # 深拷贝：外部修改不影响内部日志
    log[0]["command"] = -1
    assert m.command_log[0]["command"] == nm.NMT_CMD_START
