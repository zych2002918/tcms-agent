"""多节点 TCMS 总线仿真：VCU（主控）/ BCU（制动）/ BMS（能源）独立节点。

对应真实列控网络：TCMS 主控节点 + 制动控制单元 + 电池管理系统分别挂在
同一 CAN 总线上，各自按周期发送本节点报文。支持**节点级失活**（断电/
通信中断场景），用于验证"单节点故障不影响其他节点"的总线健壮性。

节点-报文归属:
    VCU (车辆控制单元): TCMS_Heartbeat / VehicleSpeed / TractionBrakeHandle
                        / DoorControl / AlarmEvent
    BCU (制动控制单元): PantographStatus / BrakeSystem
    BMS (电池管理系统): EnergyStatus
"""

import threading
import time

from can import Bus, Message

from . import protocol as proto

# 节点 → 报文 ID 映射（与真实 TCMS 拓扑一致）
NODE_MESSAGES: dict[str, list[int]] = {
    "VCU": [
        proto.TCMS_HEARTBEAT,
        proto.VEHICLE_SPEED,
        proto.TRACTION_BRAKE_HANDLE,
        proto.DOOR_CONTROL,
        proto.ALARM_EVENT,
    ],
    "BCU": [
        proto.PANTOGRAPH_STATUS,
        proto.BRAKE_SYSTEM,
    ],
    "BMS": [
        proto.ENERGY_STATUS,
    ],
}


class MultiNodeSimulator:
    """多节点仿真器：每个节点独立线程发送本节点报文，可单独失活。"""

    def __init__(self, bus: Bus, db):
        self.bus = bus
        self.db = db
        self._running = False
        self._threads: dict[str, threading.Thread] = {}
        self._disabled: set[str] = set()  # 失活节点集合
        self._hb_counter = 0  # VCU 心跳计数
        self.error_count = 0  # 节点线程吞掉的异常计数（可观测性）
        self.last_error: str | None = None

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动全部节点。"""
        if self._running:
            return
        self._running = True
        for node in NODE_MESSAGES:
            self._threads[node] = threading.Thread(
                target=self._node_loop, args=(node,), daemon=True
            )
            self._threads[node].start()

    def stop(self) -> None:
        """停止全部节点。"""
        self._running = False
        for t in self._threads.values():
            t.join(timeout=1.0)
        self._threads.clear()

    # ---- 节点控制（故障注入） ----

    def disable_node(self, node: str) -> None:
        """失活节点（模拟断电/通信中断），该节点全部报文停止。"""
        if node not in NODE_MESSAGES:
            raise ValueError(f"未知节点: {node}")
        self._disabled.add(node)

    def enable_node(self, node: str) -> None:
        """恢复节点。"""
        self._disabled.discard(node)

    def send_alarm(self, alarm_code: int, level: int, **flags: bool) -> None:
        """事件触发：由 VCU 发送一条报警报文。"""
        signals = {"AlarmCode": alarm_code, "AlarmLevel": level}
        for flag in ("Overspeed", "DoorNotClosed", "FireAlarm", "PantographDrop"):
            signals[flag] = int(flags.get(flag, False))
        self._send("AlarmEvent", **signals)

    @property
    def active_nodes(self) -> list[str]:
        """当前活跃节点。"""
        return [n for n in NODE_MESSAGES if n not in self._disabled]

    # ---- 内部 ----

    def _node_loop(self, node: str) -> None:
        # 节点启动后按各报文周期发送（AlarmEvent 为事件型，不周期发送）
        periods = {
            proto.TCMS_HEARTBEAT: 0.100,
            proto.VEHICLE_SPEED: 0.100,
            proto.TRACTION_BRAKE_HANDLE: 0.050,
            proto.DOOR_CONTROL: 0.100,
            proto.PANTOGRAPH_STATUS: 0.500,
            proto.BRAKE_SYSTEM: 0.100,
            proto.ENERGY_STATUS: 0.500,
        }
        next_tick = {mid: time.monotonic() for mid in NODE_MESSAGES[node]}
        while self._running:
            if node in self._disabled:
                time.sleep(0.02)
                continue
            now = time.monotonic()
            for mid in list(NODE_MESSAGES[node]):
                if mid in (proto.ALARM_EVENT,):
                    continue  # 事件型报文不周期发送
                if now >= next_tick[mid]:
                    try:
                        self._tick(node, mid)
                    except Exception as exc:  # noqa: BLE001 吞异常防线程崩，但计数可观测
                        self.error_count += 1
                        self.last_error = f"{node}/{mid}: {exc}"
                    next_tick[mid] = now + periods[mid]
            time.sleep(0.005)

    def _tick(self, node: str, message_id: int) -> None:
        if message_id == proto.TCMS_HEARTBEAT:
            self._send(
                "TCMS_Heartbeat", NodeStatus=2, RunMode=2, HeartbeatCounter=self._hb_counter % 256
            )
            self._hb_counter += 1
        elif message_id == proto.VEHICLE_SPEED:
            self._send("VehicleSpeed", SpeedKmh=0.0, SpeedValid=1, SpeedSource=1)
        elif message_id == proto.TRACTION_BRAKE_HANDLE:
            self._send(
                "TractionBrakeHandle",
                HandlePosition=0,
                Direction=0,
                TractionActive=0,
                BrakeActive=0,
            )
        elif message_id == proto.DOOR_CONTROL:
            self._send(
                "DoorControl",
                Door1State=0,
                Door2State=0,
                Door3State=0,
                Door4State=0,
                AllDoorsClosed=1,
                DoorOpenPermit=0,
            )
        elif message_id == proto.PANTOGRAPH_STATUS:
            self._send(
                "PantographStatus",
                PantographUp=1,
                PantographFault=0,
                LineVoltage=25000,
                PantographPressure=5.0,
            )
        elif message_id == proto.BRAKE_SYSTEM:
            self._send(
                "BrakeSystem",
                BrakeCylinderPressure=0.0,
                EmergencyBrakeActive=0,
                BrakeFault=0,
                ReservePressureLow=0,
            )
        elif message_id == proto.ENERGY_STATUS:
            self._send(
                "EnergyStatus",
                SocPercent=80,
                BatteryVoltage=750.0,
                BatteryCurrent=-50.0,
                BatteryTemp=35.0,
                ChargeState=2,
            )

    def _send(self, name: str, **signals) -> None:
        data = proto.encode(self.db, name, **signals)
        self.bus.send(
            Message(
                arbitration_id=self.db.get_message_by_name(name).frame_id,
                data=data,
                is_extended_id=False,
            )
        )
