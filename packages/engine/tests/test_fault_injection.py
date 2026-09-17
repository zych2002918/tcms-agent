"""故障注入与边界值验证：编码钳位、越界保护、丢报与超速联动。"""

import time

import pytest
from can import Message
from cantools.database.errors import EncodeError

from tcms import protocol as proto
from tcms.parser import collect, decode

OVERSPEED_LIMIT = proto.OVERSPEED_LIMIT_KMH  # 160 km/h


def _encode(db, name, **signals):
    for sig in db.get_message_by_name(name).signals:
        signals.setdefault(sig.name, 0)
    return db.encode_message(name, signals)


def test_speed_at_physical_max_encodes(db):
    """车速 200km/h（物理上限）应可正常编码。"""
    data = _encode(db, "VehicleSpeed", SpeedKmh=200.0)
    assert len(data) == 8


def test_speed_over_max_rejected(db):
    """车速 200.1km/h 超出物理上限，编码应拒绝。"""
    with pytest.raises(EncodeError):
        _encode(db, "VehicleSpeed", SpeedKmh=200.1)


def test_negative_speed_rejected(db):
    """负车速超出信号值域，编码应拒绝。"""
    with pytest.raises(EncodeError):
        _encode(db, "VehicleSpeed", SpeedKmh=-0.1)


def test_handle_over_max_rejected(db):
    """手柄级位 17 超出 0-16 级位上限，编码应拒绝。"""
    with pytest.raises(EncodeError):
        _encode(db, "TractionBrakeHandle", HandlePosition=17)


def test_soc_100_encodes(db):
    """SOC=100%（上限）可编码。"""
    assert len(_encode(db, "EnergyStatus", SocPercent=100)) == 8


def test_soc_over_max_rejected(db):
    """SOC=101% 越界，编码应拒绝。"""
    with pytest.raises(EncodeError):
        _encode(db, "EnergyStatus", SocPercent=101)


def test_battery_temp_minus40_encodes(db):
    """电池温度 -40℃（下限）可编码。"""
    assert len(_encode(db, "EnergyStatus", BatteryTemp=-40)) == 8


def test_battery_temp_below_min_rejected(db):
    """电池温度 -41℃ 越界，编码应拒绝。"""
    with pytest.raises(EncodeError):
        _encode(db, "EnergyStatus", BatteryTemp=-41)


def test_overspeed_alarm_flow(bus, db, simulator):
    """超速联动：车速超过 160km/h 后触发超速报警事件。"""
    simulator.set_speed(165.0)
    simulator.send_alarm(1, 2, Overspeed=True)
    time.sleep(0.1)
    collected = collect(bus, 0.3, {proto.VEHICLE_SPEED, proto.ALARM_EVENT}, db)
    speed = collected[proto.VEHICLE_SPEED][-1]["SpeedKmh"]
    alarm = collected[proto.ALARM_EVENT][-1]
    assert speed > OVERSPEED_LIMIT
    assert alarm["Overspeed"] == 1
    assert alarm["AlarmLevel"] in ("Severe", "Emergency")


def test_heartbeat_loss_detection(bus, db, simulator):
    """丢报检测：心跳停止后，0.8s 窗口内应为 0 帧。"""
    simulator.stop_message(proto.TCMS_HEARTBEAT)
    time.sleep(0.25)
    while bus.recv(timeout=0.01) is not None:
        pass
    from tcms.parser import count_frames

    assert count_frames(bus, 0.8, proto.TCMS_HEARTBEAT) == 0


def test_single_message_loss_others_survive(bus, db, simulator):
    """部分丢报：仅心跳丢失时，其他周期报文应不受影响。"""
    simulator.stop_message(proto.TCMS_HEARTBEAT)
    time.sleep(0.25)
    while bus.recv(timeout=0.01) is not None:
        pass
    from tcms.parser import count_frames

    assert count_frames(bus, 0.8, proto.TCMS_HEARTBEAT) == 0
    assert count_frames(bus, 0.8, proto.VEHICLE_SPEED) >= 5


def test_heartbeat_jitter_counter_integrity(bus, db):
    """时钟抖动注入：带抖动的仿真器心跳计数仍保持单调递增。"""
    from tcms.simulator import TCMSNodeSimulator

    jitter_sim = TCMSNodeSimulator(bus, db, heartbeat_jitter=0.02)
    jitter_sim.start()
    time.sleep(0.3)
    jitter_sim.stop()
    collected = collect(bus, 0.4, {proto.TCMS_HEARTBEAT}, db)
    frames = collected[proto.TCMS_HEARTBEAT]
    counters = [f["HeartbeatCounter"] for f in frames]
    assert all(counters[i + 1] == (counters[i] + 1) % 256 for i in range(len(counters) - 1)), (
        f"抖动下心跳计数异常: {counters}"
    )


def test_door_fault_blocks_closed_flag(bus, db, simulator):
    """车门故障安全：任一车门故障时 AllDoorsClosed 不应置 1。"""
    simulator.set_door_state(1, 2)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.DOOR_CONTROL}, db)
    f = collected[proto.DOOR_CONTROL][-1]
    assert f["Door2State"] == "Fault"
    assert f["AllDoorsClosed"] == 0


def test_raw_frame_reparse_matches(bus, db, simulator):
    """原始帧往返：编码后数据可直接解码，无信息丢失。"""
    raw = _encode(
        db,
        "EnergyStatus",
        SocPercent=65,
        BatteryVoltage=742.3,
        BatteryCurrent=-120.5,
        BatteryTemp=28,
        ChargeState=1,
    )
    msg = Message(arbitration_id=proto.ENERGY_STATUS, data=raw, is_extended_id=False)
    decoded = decode(db, msg)
    assert decoded["SocPercent"] == 65
    assert decoded["BatteryVoltage"] == pytest.approx(742.3)
    assert decoded["BatteryCurrent"] == pytest.approx(-120.5)
    assert decoded["ChargeState"] == "Charging"


def test_unknown_frame_id_rejected(db):
    """未知报文 ID（非协议定义）不应被 DBC 解码。"""
    import cantools

    msg = Message(arbitration_id=0x7FE, data=bytes(8), is_extended_id=False)
    with pytest.raises((KeyError, cantools.database.errors.Error)):
        decode(db, msg)
