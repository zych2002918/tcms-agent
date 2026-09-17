"""A6 多网段拓扑：多总线 + 异步缓冲网关转发测试。

网关是独立设备（非同步透传）：帧先进入网关接收缓冲（FIFO），
按处理时延（latency）到期后由 step() 泵出，经过滤表判定转发/丢弃；
缓冲满溢出丢弃新帧；足迹（trace）防环。测试全部用虚拟时钟确定性驱动。

覆盖：拓扑构建（段/网关）、白名单/黑名单规则、网段隔离、异步时延、
缓冲溢出、级联扩散、足迹防环、转发统计与审计日志、热插拔、错误处理。
"""

import can
import pytest
from can import Bus, Message

from tcms import network, timebase
from tcms import protocol as proto


@pytest.fixture
def vclock():
    clock = timebase.VirtualClock(mode="virtual")
    yield clock


def _pump_all(net, max_steps=10):
    """驱动网关直到所有缓冲清空（不依赖网关插入顺序）。"""
    for _ in range(max_steps):
        net.step()
        if all(v["buffered"] == 0 for v in net.gateway_stats().values()):
            return
    raise AssertionError("网关缓冲未在预期步数内清空")


@pytest.fixture
def two_segments(vclock):
    """两个网段（propulsion / brake）的拓扑。"""
    bus_a = Bus(interface="virtual", channel="seg-prop", receive_own_messages=True)
    bus_b = Bus(interface="virtual", channel="seg-brake", receive_own_messages=True)
    net = network.BusNetwork({"propulsion": bus_a, "brake": bus_b}, clock=vclock)
    yield net
    bus_a.shutdown()
    bus_b.shutdown()


@pytest.fixture
def three_segments(vclock):
    """三个网段（propulsion / brake / doors），propulsion→brake→doors。"""
    buses = {
        "propulsion": Bus(interface="virtual", channel="seg-p", receive_own_messages=True),
        "brake": Bus(interface="virtual", channel="seg-b", receive_own_messages=True),
        "doors": Bus(interface="virtual", channel="seg-d", receive_own_messages=True),
    }
    net = network.BusNetwork(buses, clock=vclock)
    yield net
    for b in buses.values():
        b.shutdown()


def _msg(arb_id: int, data: bytes = b"\x01\x02") -> Message:
    return Message(arbitration_id=arb_id, data=data, is_extended_id=False)


# ---- 拓扑构建 ----


def test_network_init_requires_segments():
    with pytest.raises(ValueError, match="至少"):
        network.BusNetwork({})


def test_add_gateway_basic(two_segments):
    gw = two_segments.add_gateway("gw1", "propulsion", "brake", allow_ids=[proto.TCMS_HEARTBEAT])
    assert gw.name == "gw1"
    assert two_segments.gateways == ["gw1"]
    assert two_segments.segments == ["brake", "propulsion"]
    # 设备参数默认值：无时延（立即到期）、缓冲 64
    assert gw.latency == 0.0
    assert gw.capacity == 64


def test_add_gateway_duplicate_rejected(two_segments):
    two_segments.add_gateway("gw1", "propulsion", "brake")
    with pytest.raises(ValueError, match="已存在"):
        two_segments.add_gateway("gw1", "brake", "propulsion")


def test_add_gateway_missing_segment_rejected(two_segments):
    with pytest.raises(ValueError, match="源网段不存在"):
        two_segments.add_gateway("gw1", "nope", "brake")
    with pytest.raises(ValueError, match="目标网段不存在"):
        two_segments.add_gateway("gw2", "propulsion", "nope")


def test_add_gateway_same_segment_rejected(two_segments):
    with pytest.raises(ValueError, match="不能同一网段"):
        two_segments.add_gateway("gw1", "propulsion", "propulsion")


def test_add_gateway_bad_rule_rejected(two_segments):
    with pytest.raises(ValueError, match="未知规则"):
        two_segments.add_gateway("gw1", "propulsion", "brake", rule="bogus")


def test_add_gateway_negative_latency_rejected(two_segments):
    with pytest.raises(ValueError, match="时延不能为负"):
        two_segments.add_gateway("gw1", "propulsion", "brake", latency=-0.1)


def test_add_gateway_zero_capacity_rejected(two_segments):
    with pytest.raises(ValueError, match="容量至少"):
        two_segments.add_gateway("gw1", "propulsion", "brake", capacity=0)


def test_add_segment_duplicate_rejected(two_segments):
    with pytest.raises(ValueError, match="已存在"):
        two_segments.add_segment("propulsion", two_segments._buses["propulsion"])


# ---- 转发规则（异步：send → step → 目标段可见）----


def test_allow_rule_forwards_only_allowed(two_segments):
    two_segments.add_gateway("gw1", "propulsion", "brake", allow_ids=[proto.TCMS_HEARTBEAT])
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))  # 允许
    two_segments.send("propulsion", _msg(proto.VEHICLE_SPEED))  # 拒绝
    _pump_all(two_segments)
    msg = two_segments.recv_from("brake", timeout=0.2)
    assert msg is not None
    assert msg.arbitration_id == proto.TCMS_HEARTBEAT
    assert two_segments.recv_from("brake", timeout=0.05) is None  # 车速未转发
    st = two_segments.gateway_stats()["gw1"]
    assert st["forwarded"] == 1
    assert st["dropped"] == 1


def test_allow_rule_empty_means_all(two_segments):
    """白名单为空 = 全部转发。"""
    two_segments.add_gateway("gw1", "propulsion", "brake")
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    two_segments.send("propulsion", _msg(proto.VEHICLE_SPEED))
    _pump_all(two_segments)
    st = two_segments.gateway_stats()["gw1"]
    assert st["forwarded"] == 2
    assert st["dropped"] == 0


def test_block_rule_forwards_except_blocked(two_segments):
    two_segments.add_gateway(
        "gw1", "propulsion", "brake", block_ids=[proto.ALARM_EVENT], rule=network.RULE_BLOCK
    )
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    two_segments.send("propulsion", _msg(proto.ALARM_EVENT))
    _pump_all(two_segments)
    st = two_segments.gateway_stats()["gw1"]
    assert st["forwarded"] == 1  # 心跳转发
    assert st["dropped"] == 1  # 报警被黑名单拦截


def test_segment_isolation_no_gateway(two_segments):
    """无网关的两段互不可见（隔离）。"""
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    assert two_segments.recv_from("brake", timeout=0.05) is None


# ---- 异步转发语义（现实化核心）----


def test_async_not_forwarded_until_step(vclock, two_segments):
    """转发是异步的：send 后目标段不可见，时钟推进+step 后才可见。"""
    two_segments.add_gateway(
        "gw1", "propulsion", "brake", allow_ids=[proto.TCMS_HEARTBEAT], latency=0.05
    )
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    # 时延未到：目标段无帧
    assert two_segments.recv_from("brake", timeout=0.05) is None
    # 时钟推进 0.05s（未 step）：网关缓冲到期但未泵出
    vclock.advance(0.05)
    assert two_segments.recv_from("brake", timeout=0.05) is None
    # step 泵出到期帧 → 目标段可见
    two_segments.step()
    assert two_segments.recv_from("brake", timeout=0.05) is not None


def test_buffer_overflow_drops_newest(two_segments):
    """缓冲满：新帧被丢弃（溢出），不阻塞发送方。"""
    two_segments.add_gateway("gw1", "propulsion", "brake", capacity=2)
    for i in range(4):
        two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT, bytes([i])))
    st = two_segments.gateway_stats()["gw1"]
    assert st["overflow_dropped"] == 2  # 第 3、4 帧溢出丢弃
    assert st["forwarded"] == 0  # 尚未泵出
    _pump_all(two_segments)
    assert two_segments.gateway_stats()["gw1"]["forwarded"] == 2


def test_buffered_count_reports_occupancy(two_segments):
    """缓冲占用可观测。"""
    gw = two_segments.add_gateway("gw1", "propulsion", "brake")
    assert two_segments.gateway_stats()["gw1"]["buffered"] == 0
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    assert gw.buffered == 1
    two_segments.step()
    assert gw.buffered == 0


def test_cascade_with_latency_two_steps(vclock, three_segments):
    """级联+时延：三网段需两拍才能到达最远端（逐网关推进）。"""
    three_segments.add_gateway("gw1", "propulsion", "brake", latency=0.02)
    three_segments.add_gateway(
        "gw2", "brake", "doors", allow_ids=[proto.TCMS_HEARTBEAT], latency=0.02
    )
    three_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    # 第一拍：gw1 泵出 → brake 可见，gw2 缓冲收帧（未到期）
    vclock.advance(0.02)
    three_segments.step()
    assert three_segments.recv_from("brake", timeout=0.05) is not None
    assert three_segments.recv_from("doors", timeout=0.05) is None
    # 第二拍：gw2 泵出 → doors 可见
    vclock.advance(0.02)
    three_segments.step()
    assert three_segments.recv_from("doors", timeout=0.05) is not None


# ---- 数据面 ----


def test_send_unknown_segment_rejected(two_segments):
    with pytest.raises(ValueError, match="网段不存在"):
        two_segments.send("nope", _msg(proto.TCMS_HEARTBEAT))


def test_chain_forwarding_three_segments(three_segments):
    """级联转发：propulsion →(gw1)→ brake →(gw2)→ doors。"""
    three_segments.add_gateway("gw1", "propulsion", "brake")
    three_segments.add_gateway("gw2", "brake", "doors", allow_ids=[proto.TCMS_HEARTBEAT])
    three_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    _pump_all(three_segments)
    assert three_segments.recv_from("brake", timeout=0.2) is not None
    assert three_segments.recv_from("doors", timeout=0.2) is not None
    st = three_segments.gateway_stats()
    assert st["gw1"]["forwarded"] == 1
    assert st["gw2"]["forwarded"] == 1


def test_forward_log_audit(two_segments):
    """审计日志记录每帧的网关处理结果与时间戳。"""
    two_segments.add_gateway("gw1", "propulsion", "brake", allow_ids=[proto.TCMS_HEARTBEAT])
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    two_segments.send("propulsion", _msg(proto.VEHICLE_SPEED))
    _pump_all(two_segments)
    log = two_segments.forward_log()
    assert len(log) == 2  # 两帧都被网关处理
    ok = [e for e in log if e["forwarded"]]
    nok = [e for e in log if not e["forwarded"]]
    assert len(ok) == 1 and ok[0]["arb_id"] == proto.TCMS_HEARTBEAT
    assert ok[0]["dropped_reason"] is None
    assert len(nok) == 1 and nok[0]["arb_id"] == proto.VEHICLE_SPEED
    assert nok[0]["dropped_reason"] == network.DROP_RULE
    # 时间戳：enqueued <= forwarded
    for e in log:
        assert e["enqueued_ts"] <= e["forwarded_ts"]
    # 深拷贝语义：外部修改不影响内部
    log[0]["gateway"] = "hacked"
    assert two_segments.forward_log()[0]["gateway"] == "gw1"


def test_reset_stats(two_segments):
    two_segments.add_gateway("gw1", "propulsion", "brake")
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    _pump_all(two_segments)
    two_segments.reset_stats()
    st = two_segments.gateway_stats()["gw1"]
    assert st["forwarded"] == 0
    assert st["overflow_dropped"] == 0
    assert two_segments.forward_log() == []


def test_recv_any_cross_segment(two_segments):
    """跨网段统一接收视角。"""
    two_segments.add_gateway("gw1", "propulsion", "brake")
    two_segments.send("propulsion", _msg(proto.VEHICLE_SPEED))
    got = two_segments.recv_any(timeout=0.2)
    assert got is not None
    seg, msg = got
    assert seg in ("brake", "propulsion")  # 两个段都能看到
    assert msg.arbitration_id == proto.VEHICLE_SPEED


def test_hot_add_segment(two_segments):
    """运行时热插拔网段。"""
    bus_c = Bus(interface="virtual", channel="seg-c", receive_own_messages=True)
    try:
        two_segments.add_segment("doors", bus_c)
        two_segments.add_gateway("gw1", "propulsion", "doors")
        two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
        _pump_all(two_segments)
        assert two_segments.recv_from("doors", timeout=0.2) is not None
    finally:
        bus_c.shutdown()


def test_gateway_stats_shape(two_segments):
    two_segments.add_gateway("gw1", "propulsion", "brake")
    two_segments.add_gateway("gw2", "brake", "propulsion")
    st = two_segments.gateway_stats()
    assert set(st) == {"gw1", "gw2"}
    for v in st.values():
        assert v == {"forwarded": 0, "dropped": 0, "overflow_dropped": 0, "buffered": 0}


# ---- 防环 / 故障容错 ----


def test_loop_footprint_prevents_infinite(two_segments):
    """双向网关：帧回环一次即被足迹拦截，不会无限转发。"""
    two_segments.add_gateway("gw1", "propulsion", "brake")
    two_segments.add_gateway("gw2", "brake", "propulsion")
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    _pump_all(two_segments)
    # 帧路径：propulsion →gw1→ brake（gw1.forwarded=1）
    #         brake  →gw2→ propulsion（gw2.forwarded=1，回环一次）
    #         propulsion 出站 gw1 收到（足迹 {gw1,gw2}）→ 拒绝，不再扩散
    st = two_segments.gateway_stats()
    assert st["gw1"]["forwarded"] == 1
    assert st["gw2"]["forwarded"] == 1
    # 缓冲已空 + 再泵也不增长 → 无无限循环
    two_segments.step()
    assert two_segments.gateway_stats()["gw1"]["forwarded"] == 1
    assert two_segments.gateway_stats()["gw2"]["forwarded"] == 1


def test_send_bus_error_returns_false(monkeypatch, two_segments):
    """本地发送失败 → send() 返回 False（不抛异常）。"""

    class BoomBus:
        def send(self, msg):
            raise can.CanError("bus broken")

        def recv(self, timeout=0.0):
            return None

    monkeypatch.setitem(two_segments._buses, "propulsion", BoomBus())
    assert two_segments.send("propulsion", _msg(0x100)) is False


def test_forward_bus_error_counts_forwarded(monkeypatch, two_segments):
    """网关目标段发送失败 → 仍计 forwarded，日志记录 forwarded=False。"""

    class BoomBus:
        def send(self, msg):
            raise can.CanError("dst broken")

        def recv(self, timeout=0.0):
            return None

    two_segments.add_gateway("gw1", "propulsion", "brake")
    monkeypatch.setitem(two_segments._buses, "brake", BoomBus())
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    two_segments.step()
    st = two_segments.gateway_stats()["gw1"]
    assert st["forwarded"] == 1
    assert two_segments.forward_log()[0]["forwarded"] is False


def test_recv_from_unknown_segment_rejected(two_segments):
    with pytest.raises(ValueError, match="网段不存在"):
        two_segments.recv_from("nope")


def test_recv_any_blocking_wait(two_segments):
    """空总线 + timeout>0 → 阻塞等待第一段。"""
    two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))
    # 清空两个段
    while two_segments.recv_any(timeout=0.0) is not None:
        pass
    assert two_segments.recv_any(timeout=0.0) is None  # 空 → None
    # 阻塞等待：先发后收
    got = two_segments.recv_any(timeout=0.3)
    assert got is None or got[1].arbitration_id == proto.TCMS_HEARTBEAT


def test_recv_any_blocking_gets_message(two_segments):
    """阻塞等待窗口内有报文到达 → 返回该报文。"""
    import threading

    def delayed_send():
        import time

        time.sleep(0.1)
        two_segments.send("propulsion", _msg(proto.TCMS_HEARTBEAT))

    t = threading.Thread(target=delayed_send, daemon=True)
    t.start()
    got = two_segments.recv_any(timeout=1.0)
    assert got is not None
    seg, msg = got
    assert msg.arbitration_id == proto.TCMS_HEARTBEAT
    t.join(timeout=2.0)
