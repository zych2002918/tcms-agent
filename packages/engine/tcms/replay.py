"""完整回放链（Replay Chain）—— .asc 日志 → 业务逻辑 → 证据断言。

对标真实测试流程：现场抓取的 CAN 日志（Vector .asc / PCAN .trc）回放到
仿真平台，驱动 TCMS 业务逻辑（联锁/ATP/看门狗/紧急制动），并统一记录
全部中间事件——"真实数据驱动回归"：日志里藏着什么故障，回放后业务
逻辑就必须在同一个时间轴上给出同样的响应，证据链完整可追溯。

核心组件：
    - ReplayEngine：虚拟时钟驱动的回放引擎。按日志帧时间戳推进虚拟时钟，
      每帧依次触发：bus 记录 → 业务评估（interlocks/ATP/watchdog/EBM）→
      事件记录。全程不 sleep（纯虚拟时间），毫秒级回放整段日志。
    - ReplayChain：一站式入口——解析 .asc → 建 RecordedBus → 建业务逻辑
      对象 → 回放 → 返回 {frames, recorder, result, report}。

用法：
    chain = ReplayChain.from_asc(path)      # 或 ReplayChain(frames)
    report = chain.run()                     # 回放整段日志
    report["alerts"]                         # 回放期间触发的告警/故障列表
    report["ebm_triggered"]                  # EBM 是否被触发
"""

from __future__ import annotations

from . import atp, canlog, ebm, interlocks, recorder, watchdogs
from . import protocol as proto

# 回放期间可观测的告警类别（业务评估输出）
ALERT_OVERRIDE_OVERRUN = "interlock_violation"
ALERT_EBM_TRIGGER = "ebm_trigger"
ALERT_EBM_RELEASE = "ebm_release"
ALERT_ATP_LEVEL = "atp_supervision"
ALERT_WATCHDOG_FAULT = "watchdog_fault"


class ReplayEngine:
    """虚拟时钟驱动的回放引擎：帧 → 业务评估 → 事件记录。

    时间轴：帧时间戳直接作为虚拟时钟（frame["ts"]），回放期间
    self.now 跟随帧时间推进；所有业务评估使用同一虚拟时钟，
    保证跨模块时间一致性（与 A1 虚拟时间基同思路）。
    """

    def __init__(
        self,
        frames: list[dict],
        recorder_obj: recorder.EventRecorder | None = None,
        ebm_manager: ebm.EmergencyBrakeManager | None = None,
        atp_supervisor: atp.SpeedSupervisor | None = None,
        watchdog_table: watchdogs.NodeHealthTable | None = None,
        door_motion_threshold: float = interlocks.MOTION_THRESHOLD_KMH,
    ):
        self.frames = list(frames)
        self.rec = recorder_obj or recorder.EventRecorder()
        self.ebm = ebm_manager or ebm.EmergencyBrakeManager()
        self.atp = atp_supervisor or atp.SpeedSupervisor()
        # 看门狗必须用虚拟时钟（self.now 跟随帧时间戳推进），否则
        # 真实 monotonic 时钟下所有帧"同时到达"，丢帧判定失效
        self.watchdogs = watchdog_table or watchdogs.NodeHealthTable(
            now=lambda: self.now,
        )
        self.door_motion_threshold = door_motion_threshold
        self.now = 0.0  # 虚拟时钟（当前帧时间戳）
        self.alerts: list[dict] = []  # 回放期间触发的告警/故障
        self._door_states = [0, 0, 0, 0]  # 最近一帧车门状态（累积）
        self._last_speed_kmh = 0.0  # 最近一帧车速
        self._last_speed_valid = True
        self._ebm_hooked = False

    # ---- 业务评估 ----

    def _evaluate_frame(self, frame: dict) -> None:
        """对一帧做业务评估（与真实逻辑一致，供断言）。"""
        arb_id = frame["arb_id"]
        data = frame["data"]
        # 1) 看门狗：心跳帧喂健康表
        if arb_id == proto.TCMS_HEARTBEAT:
            self.watchdogs.feed("vcu")
        # 2) 联锁：车门状态（0x400）+ 速度（0x200）
        if arb_id == proto.DOOR_CONTROL:
            self._door_states = [
                (data[0] >> 0) & 0x03,
                (data[0] >> 2) & 0x03,
                (data[0] >> 4) & 0x03,
                (data[0] >> 6) & 0x03,
            ]
        elif arb_id == proto.VEHICLE_SPEED:
            speed_raw = int.from_bytes(data[0:2], "little")
            speed_kmh = speed_raw / 10.0
            speed_valid = bool(data[2] & 0x01)
            self._last_speed_kmh = speed_kmh
            self._last_speed_valid = speed_valid
            # 3) 联锁：门-车冲突
            conflict, reason = interlocks.door_motion_conflict(
                self._door_states,
                speed_kmh,
                speed_valid=speed_valid,
            )
            if conflict:
                self._alert(ALERT_OVERRIDE_OVERRUN, reason, speed_kmh=speed_kmh)
            # 4) ATP 监督
            level = self.atp.evaluate(speed_kmh, speed_valid)
            if level != atp.SUPERVISION_NONE:
                self._alert(ALERT_ATP_LEVEL, level, speed_kmh=speed_kmh)
            # 5) EBM：ATP 触发 EBI 或联锁冲突 → 紧急制动
            if level == atp.SUPERVISION_EBI or conflict:
                self.ebm.trigger("overspeed" if level == atp.SUPERVISION_EBI else "door_open")
                self._alert(
                    ALERT_EBM_TRIGGER, "overspeed" if level == atp.SUPERVISION_EBI else "door_open"
                )
            elif level == atp.SUPERVISION_NONE and not conflict:
                self.ebm.release_condition(speed_kmh, speed_valid)

    def _alert(self, kind: str, detail: str, **payload) -> None:
        """记录告警（写入 alerts 列表 + 事件记录器）。"""
        ev = self.rec.record_event(
            recorder.EVENT_EBM
            if kind in (ALERT_EBM_TRIGGER, ALERT_EBM_RELEASE)
            else recorder.EVENT_CAN_RX,
            arb_id=None,
            category="replay",
            message=f"{kind}:{detail}",
            payload={"ts": self.now, **payload},
        )
        self.alerts.append({"kind": kind, "detail": detail, "ts": self.now, "event": ev})

    # ---- 回放 ----

    def run(self, on_frame=None) -> int:
        """回放全部帧（虚拟时钟，不 sleep）。返回帧数。

        每帧：推进 now → 业务评估 → 看门狗周期评估 → 回调（可空）。
        """
        self.now = 0.0
        self.alerts.clear()
        for frame in self.frames:
            self.now = frame["ts"]
            self._evaluate_frame(frame)
            # 周期看门狗评估：每次心跳后检查健康（丢帧/离线判定）
            self._evaluate_watchdogs()
            if on_frame is not None:
                on_frame(frame)
        return len(self.frames)

    def _evaluate_watchdogs(self) -> None:
        """看门狗周期评估：节点离线/故障告警。"""
        states = self.watchdogs.evaluate()
        for node, state in states.items():
            if state == watchdogs.STATE_FAULT:
                self._alert(ALERT_WATCHDOG_FAULT, node)

    # ---- 结果 ----

    def report(self) -> dict:
        """回放报告：帧数、告警、EBM 状态、看门狗状态。"""
        return {
            "frames": len(self.frames),
            "alerts": list(self.alerts),
            "alert_kinds": sorted({a["kind"] for a in self.alerts}),
            "ebm_state": self.ebm.state,
            "ebm_triggered": any(a["kind"] == ALERT_EBM_TRIGGER for a in self.alerts),
            "watchdog_states": self.watchdogs.evaluate(),
            "atp_last_level": self.atp.evaluate(
                self._last_speed_kmh,
                self._last_speed_valid,
            ),
        }


class ReplayChain:
    """一站式完整回放链：.asc 日志 → 业务逻辑 → 证据断言。

    用法：
        chain = ReplayChain.from_asc("demo.asc")
        report = chain.run()
        assert report["frames"] == expected
    """

    def __init__(
        self,
        frames: list[dict],
        ebm_manager: ebm.EmergencyBrakeManager | None = None,
        atp_supervisor: atp.SpeedSupervisor | None = None,
        recorder_obj: recorder.EventRecorder | None = None,
    ):
        self.frames = list(frames)
        self.ebm = ebm_manager or ebm.EmergencyBrakeManager()
        self.atp = atp_supervisor or atp.SpeedSupervisor()
        self.rec = recorder_obj or recorder.EventRecorder()
        self.engine = ReplayEngine(
            self.frames,
            recorder_obj=self.rec,
            ebm_manager=self.ebm,
            atp_supervisor=self.atp,
        )

    @classmethod
    def from_asc(cls, path: str) -> "ReplayChain":
        """从 .asc 文件构建回放链。"""
        return cls(canlog.parse_asc_file(path))

    @classmethod
    def from_text(cls, text: str) -> "ReplayChain":
        """从 .asc 文本构建回放链（测试便利）。"""
        return cls(canlog.parse_asc(text))

    def run(self) -> dict:
        """回放整段日志，返回报告。"""
        self.engine.run()
        return self.engine.report()
