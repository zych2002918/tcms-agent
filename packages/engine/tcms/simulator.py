"""TCMS 列车网络控制器仿真器。

在虚拟 CAN 总线上按 DBC 定义的周期模拟机车 TCMS 节点报文，
支持故障注入（停止发送 / 超界信号 / 异常事件），作为被测对象
（DUT）供自动化测试驱动。

用法:
    import can
    bus = can.Bus(interface="virtual")
    sim = TCMSNodeSimulator(bus, db)
    sim.start()          # 按周期发送全部周期报文
    sim.send_alarm(...)  # 触发一次报警事件
    sim.stop_message(...) # 停止某个报文（模拟丢报）
    sim.stop()
"""

import threading
import time

from can import Bus, Message

from . import protocol as proto

# 13 系统域扩展帧的名义运行值（引擎按 DBC 周期真实发送；经典 8 帧在
# _tick 内按业务语义逐帧构建，扩展帧在此给出"健康运行"的名义基准）
_EXT_NOMINAL: dict[str, dict] = {
    "HVAC_CabinStatus": {
        "CabinTemp": 24.0,
        "SetTemp": 22.0,
        "CompressorState": 1,
        "HvacFaultCode": 0,
    },
    "HVAC_Monitor": {
        "FilterDirty": 0,
        "HeaterState": 0,
        "FreshAirDamper": 80,
        "CompressorHours": 1200,
        "HvacPowerKw": 18.5,
    },
    "PIS_PassengerInfo": {
        "NextStationCode": 3,
        "PisDisplayState": 0,
        "NextStopAnnounce": 0,
        "EmergencyTalkActive": 0,
        "PassengerIntercom": 0,
        "PassengerCount": 42,
        "PisFaultCode": 0,
    },
    "Light_CabinControl": {
        "CabinLightState": 1,
        "EmergencyLightActive": 0,
        "LightBrightness": 80,
        "LampGroupFault": 0,
        "LightFaultCode": 0,
    },
    "Fire_Detection": {
        "SmokeDetectorState": 0,
        "FireZone": 0,
        "ExtinguisherReady": 1,
        "DetectorTestMode": 0,
        "FireSystemFault": 0,
    },
    "Aux_Converter": {
        "AuxVoltage": 380.0,
        "AuxFrequency": 50.0,
        "AuxLoadPercent": 45,
        "AuxContactorState": 1,
        "AuxConverterFault": 0,
        "AuxInverterTemp": 42.0,
        "AuxAmbientTemp": 30.0,
    },
    "ATO_Command": {
        "AtoMode": 1,
        "AtoAvailable": 1,
        "AtoWarning": 0,
        "DoorReleaseSide": 0,
        "TargetSpeed": 80.0,
    },
    "Bogie_Monitor": {
        "Axle1Temp": 55,
        "Axle2Temp": 56,
        "Axle3Temp": 54,
        "Axle4Temp": 55,
        "GearboxTemp": 62,
        "VibrationLevel": 2.0,
        "VibrationTrend": 10,
        "BogieSensorFault": 0,
    },
    "DoorControlRear": {
        "Door5State": 0,
        "Door6State": 0,
        "Door7State": 0,
        "Door8State": 0,
        "AllRearDoorsClosed": 1,
        "RearDoorOpenPermit": 0,
        "ObstacleDetected": 0,
        "RearDoorIsolation": 0,
    },
    "Gateway_Status": {
        "MvbSegmentA": 1,
        "MvbSegmentB": 1,
        "WlanLinkQuality": 95,
        "EtherTrainBackbone": 1,
        "GatewayFault": 0,
    },
    "Brake_Wsp": {
        "WheelSpeed1": 0.0,
        "WheelSpeed2": 0.0,
        "WheelSpeed3": 0.0,
        "WheelSpeed4": 0.0,
        "SlidePercent": 0.0,
        "WspActive": 0,
        "WspFault": 0,
    },
    "Traction_Converter": {
        "ConvTemp": 48,
        "ConvFaultCode": 0,
        "TracDisable": 0,
        "DcLinkVoltage": 1500,
    },
    "Driver_Console": {
        "MasterController": 0,
        "CabActive": 1,
        "EmergencyStopPressed": 0,
        "DriverKeyPresent": 1,
    },
    "Battery_Charger": {
        "ChargerState": 1,
        "ChargerCurrent": 20.0,
        "ChargerTemp": 28.0,
        "ChargerFault": 0,
    },
}


class TCMSNodeSimulator:
    """模拟一个 TCMS 节点组：多个周期报文 + 事件报文。"""

    def __init__(self, bus: Bus, db, heartbeat_jitter: float = 0.0):
        self.bus = bus
        self.db = db
        self.heartbeat_jitter = heartbeat_jitter  # 心跳抖动（秒），模拟时钟偏差
        self._running = False
        self._threads: list[threading.Thread] = []
        self._speed = 0.0  # 当前模拟车速 km/h
        self._handle = 0  # 手柄级位
        self._direction = 0  # 方向
        self._door_states = [0, 0, 0, 0]  # 四车门状态
        self._heartbeat_counter = 0
        self.error_count = 0  # 发送线程吞掉的异常计数（可观测性）
        self.last_error: str | None = None
        # frame_id → 报文名（扩展帧名义值发送用；经典帧走 _tick 业务分支）
        self._name_by_id = {m.frame_id: m.name for m in db.messages}

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动全部周期报文发送线程（按 DBC cycle_time 派生，含 13 域扩展帧）。"""
        if self._running:
            return
        self._running = True
        for msg in self.db.messages:
            cycle = getattr(msg, "cycle_time", None) or 0
            if cycle <= 0:
                continue  # 事件型（AlarmEvent）仅在事件触发时发送
            self._threads.append(
                self._spawn(self._name_by_id[msg.frame_id], msg.frame_id, cycle / 1000.0)
            )

    def stop(self) -> None:
        """停止全部发送线程。"""
        self._running = False
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()

    # ---- 状态注入（测试驱动用） ----

    def set_speed(self, kmh: float) -> None:
        """设定模拟车速。"""
        self._speed = kmh

    def set_handle(self, position: int, direction: int = 1) -> None:
        """设定手柄级位与方向。"""
        self._handle = position
        self._direction = direction

    def set_door_state(self, door_index: int, state: int) -> None:
        """设定单个车门状态（0 关 / 1 开 / 2 故障）。"""
        if 0 <= door_index < 4:
            self._door_states[door_index] = state

    def stop_message(self, message_id: int) -> None:
        """停止发送指定报文（模拟节点丢报/总线故障）。"""
        # 通过把对应线程的发送标志置空实现：这里用名称标记
        self._stopped = getattr(self, "_stopped", set())
        self._stopped.add(message_id)

    # ---- 报文构建 ----

    def _build(self, name: str, **signals) -> Message:
        data = proto.encode(self.db, name, **signals)
        return Message(
            arbitration_id=self.db.get_message_by_name(name).frame_id,
            data=data,
            is_extended_id=False,
        )

    def _send(self, name: str, **signals) -> None:
        self.bus.send(self._build(name, **signals))

    def send_alarm(self, alarm_code: int, level: int, **flags: bool) -> None:
        """事件触发：发送一条报警报文。

        flags 支持 AlarmEvent 全部报警位：Overspeed / DoorNotClosed /
        FireAlarm(烟火) / PantographDrop / HvacFault(空调) / BogieVibration(走行部)。
        """
        signals = {"AlarmCode": alarm_code, "AlarmLevel": level}
        for flag in (
            "Overspeed",
            "DoorNotClosed",
            "FireAlarm",
            "PantographDrop",
            "HvacFault",
            "BogieVibration",
        ):
            signals[flag] = int(flags.get(flag, False))
        self._send("AlarmEvent", **signals)

    # ---- 内部 ----

    def _spawn(self, tag: str, message_id: int, period: float) -> threading.Thread:
        t = threading.Thread(target=self._loop, args=(tag, message_id, period), daemon=True)
        t.start()
        return t

    def _loop(self, tag: str, message_id: int, period: float) -> None:
        while self._running:
            if message_id not in getattr(self, "_stopped", set()):
                try:
                    self._tick(tag, message_id)
                except Exception as exc:  # noqa: BLE001 吞异常防线程崩，但计数可观测
                    self.error_count += 1
                    self.last_error = f"{tag}: {exc}"
            time.sleep(period + (self.heartbeat_jitter if tag == "TCMS_Heartbeat" else 0.0))

    def _tick(self, tag: str, message_id: int) -> None:
        name = self._name_by_id.get(message_id, tag)
        if name == "TCMS_Heartbeat":
            self._send(
                "TCMS_Heartbeat",
                NodeStatus=2,
                RunMode=2,
                HeartbeatCounter=self._heartbeat_counter % 256,
            )
            self._heartbeat_counter += 1
        elif name == "VehicleSpeed":
            self._send(
                "VehicleSpeed",
                SpeedKmh=round(self._speed, 1),
                SpeedValid=1 if self._speed >= 0 else 0,
                SpeedSource=1,
            )
        elif name == "TractionBrakeHandle":
            self._send(
                "TractionBrakeHandle",
                HandlePosition=self._handle,
                Direction=self._direction,
                TractionActive=1 if self._handle > 0 and self._direction == 1 else 0,
                BrakeActive=1 if self._direction == 2 else 0,
            )
        elif name == "DoorControl":
            self._send(
                "DoorControl",
                Door1State=self._door_states[0],
                Door2State=self._door_states[1],
                Door3State=self._door_states[2],
                Door4State=self._door_states[3],
                AllDoorsClosed=1 if all(s == 0 for s in self._door_states) else 0,
                DoorOpenPermit=1 if any(s == 1 for s in self._door_states) else 0,
            )
        elif name == "PantographStatus":
            self._send(
                "PantographStatus",
                PantographUp=1,
                PantographFault=0,
                LineVoltage=25000,
                PantographPressure=5.0,
            )
        elif name == "BrakeSystem":
            self._send(
                "BrakeSystem",
                BrakeCylinderPressure=0.0,
                EmergencyBrakeActive=0,
                BrakeFault=0,
                ReservePressureLow=0,
            )
        elif name == "EnergyStatus":
            self._send(
                "EnergyStatus",
                SocPercent=80,
                BatteryVoltage=750.0,
                BatteryCurrent=-50.0,
                BatteryTemp=35.0,
                ChargeState=2,
            )
        else:
            # 13 系统域扩展帧：按名义健康值周期发送（故障注入由场景/测试驱动）
            payload = _EXT_NOMINAL.get(name)
            if payload is None:
                return  # 未知帧：不发（不掩盖问题）
            self._send(name, **payload)
