"""TCMS 仿真器行为验证：周期发送、信号联动、事件触发、丢报注入。"""

import time

from tcms import protocol as proto
from tcms.parser import collect, count_frames


def _latest(collects, mid):
    frames = collects[mid]
    assert frames, f"报文 0x{mid:X} 未出现在总线上"
    return frames[-1]


def test_all_periodic_messages_on_bus(bus, db, simulator):
    """仿真器启动后，全部周期报文（经典 8 帧 + 13 域扩展帧）应周期出现（报警为事件型）。"""
    periodic = {m.frame_id for m in db.messages} - {proto.ALARM_EVENT}
    collected = collect(bus, 0.6, periodic, db)
    for mid in collected:
        assert collected[mid], f"0x{mid:X} 报文缺失"


def test_heartbeat_period_100ms(bus, db, simulator):
    """心跳报文周期 100ms：1.05s 内应收到约 10 帧（容差 ±3）。"""
    n = count_frames(bus, 1.05, proto.TCMS_HEARTBEAT)
    assert 7 <= n <= 13, f"心跳帧数 {n} 偏离 100ms 周期"


def test_handle_message_period_50ms(bus, db, simulator):
    """手柄报文周期 50ms：1.05s 内应收到约 20 帧（容差 ±5）。"""
    n = count_frames(bus, 1.05, proto.TRACTION_BRAKE_HANDLE)
    assert 15 <= n <= 25, f"手柄帧数 {n} 偏离 50ms 周期"


def test_heartbeat_counter_increments(bus, db, simulator):
    """心跳计数器应逐帧 +1 且回绕到 0-255。"""
    collected = collect(bus, 0.5, {proto.TCMS_HEARTBEAT}, db)
    frames = collected[proto.TCMS_HEARTBEAT]
    counters = [f["HeartbeatCounter"] for f in frames]
    assert all(counters[i + 1] == (counters[i] + 1) % 256 for i in range(len(counters) - 1)), (
        f"心跳计数未严格递增: {counters}"
    )


def test_speed_roundtrip(bus, db, simulator):
    """设置车速后，解码值应与设置值一致（0.1km/h 精度）。"""
    simulator.set_speed(85.5)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.VEHICLE_SPEED}, db)
    sig = _latest(collected, proto.VEHICLE_SPEED)["SpeedKmh"]
    assert sig == 85.5


def test_speed_valid_flag(bus, db, simulator):
    """有效车速时 SpeedValid 应为 1。"""
    simulator.set_speed(120.0)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.VEHICLE_SPEED}, db)
    assert _latest(collected, proto.VEHICLE_SPEED)["SpeedValid"] == 1


def test_traction_active_logic(bus, db, simulator):
    """手柄级位>0 且方向向前时，牵引激活标志应为 1。"""
    simulator.set_handle(8, direction=1)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.TRACTION_BRAKE_HANDLE}, db)
    f = _latest(collected, proto.TRACTION_BRAKE_HANDLE)
    assert f["HandlePosition"] == 8
    assert f["Direction"] == "Forward"
    assert f["TractionActive"] == 1
    assert f["BrakeActive"] == 0


def test_brake_active_logic(bus, db, simulator):
    """方向为向后（制动）时，制动激活标志应为 1、牵引为 0。"""
    simulator.set_handle(4, direction=2)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.TRACTION_BRAKE_HANDLE}, db)
    f = _latest(collected, proto.TRACTION_BRAKE_HANDLE)
    assert f["Direction"] == "Reverse"
    assert f["BrakeActive"] == 1
    assert f["TractionActive"] == 0


def test_all_doors_closed_flag(bus, db, simulator):
    """四车门全部关闭时 AllDoorsClosed 应为 1、开门许可为 0。"""
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.DOOR_CONTROL}, db)
    f = _latest(collected, proto.DOOR_CONTROL)
    assert f["AllDoorsClosed"] == 1
    assert f["DoorOpenPermit"] == 0


def test_door_open_sets_permit(bus, db, simulator):
    """任一车门打开时，开门许可应为 1、AllDoorsClosed 为 0。"""
    simulator.set_door_state(2, 1)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.DOOR_CONTROL}, db)
    f = _latest(collected, proto.DOOR_CONTROL)
    assert f["Door3State"] == "Open"
    assert f["AllDoorsClosed"] == 0
    assert f["DoorOpenPermit"] == 1


def test_door_fault_enum(bus, db, simulator):
    """车门故障状态应解码为 Fault 枚举。"""
    simulator.set_door_state(0, 2)
    time.sleep(0.15)
    collected = collect(bus, 0.3, {proto.DOOR_CONTROL}, db)
    f = _latest(collected, proto.DOOR_CONTROL)
    assert f["Door1State"] == "Fault"


def test_alarm_event_emission(bus, db, simulator):
    """报警事件应能触发并完整解码（代码+等级+标志位）。"""
    simulator.send_alarm(42, 3, FireAlarm=True)
    time.sleep(0.05)
    collected = collect(bus, 0.3, {proto.ALARM_EVENT}, db)
    f = _latest(collected, proto.ALARM_EVENT)
    assert f["AlarmCode"] == 42
    assert f["AlarmLevel"] == "Emergency"
    assert f["FireAlarm"] == 1


def test_pantograph_defaults(bus, db, simulator):
    """受电弓默认状态：升起、无故障、接触网电压 25000V。"""
    time.sleep(0.15)
    collected = collect(bus, 0.6, {proto.PANTOGRAPH_STATUS}, db)
    f = _latest(collected, proto.PANTOGRAPH_STATUS)
    assert f["PantographUp"] == 1
    assert f["PantographFault"] == 0
    assert f["LineVoltage"] == 25000


def test_energy_defaults(bus, db, simulator):
    """能源报文默认状态：SOC 80%、电压 750V、放电。"""
    time.sleep(0.15)
    collected = collect(bus, 0.6, {proto.ENERGY_STATUS}, db)
    f = _latest(collected, proto.ENERGY_STATUS)
    assert f["SocPercent"] == 80
    assert f["BatteryVoltage"] == 750.0
    assert f["ChargeState"] == "Discharging"


def test_stopped_message_disappears(bus, db, simulator):
    """故障注入：停止发送心跳后，总线上应不再出现该报文（丢报场景）。"""
    simulator.stop_message(proto.TCMS_HEARTBEAT)
    time.sleep(0.3)
    while bus.recv(timeout=0.01) is not None:
        pass
    n = count_frames(bus, 0.8, proto.TCMS_HEARTBEAT)
    assert n == 0, f"心跳已停止但仍收到 {n} 帧"
