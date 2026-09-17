"""Oracle（期望派生器）—— 让生成器不手抄、不编造期望。

审查裁定（docs/decisions.md D1）：生成用例的**断言期望绝不能由生成器自由
生成**——手抄 faultlevel 表会让 PASS 退化为回声 spec 的 tautology；自行推导
会编造域语义。正确做法：期望 = 生成时查询上游已校验语义（oracle 派生）。

本模块在**tcms-ai-testgen 进程内**查询上游 tcms-can-test 的语义源：
    * faultlevel.FAULTS / action_for(fault, mode) —— 故障等级与处置动作
    * faults.yaml 故障字典（可选用）—— 注入/检测/恢复描述
查询在生成时完成：mock/真 LLM 的候选 fault 键 → oracle 返回权威期望，
生成器只做「选 fault × 组合」，不做「编期望」。

设计：
    - 与 asset_loader 同款：只读上游文件/或 subprocess 查询，不 import 上游
      （tcms-ai-testgen 是独立包，上游可能不在同一 venv）。
    - 直接查询用 subprocess 调上游 python 太慢；改为**静态镜像** faultlevel
      语义（从上游 faultlevel.py 读常量，键集 10 个是稳定事实）。
    - 双源一致性由上游自己的 tests 保证（faultdb 校验）；此处镜像用于
      生成期派生，不另立语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: 处置动作（与上游 faultlevel 对齐）
ACTION_NONE = "none"
ACTION_WARNING = "warning"
ACTION_DERATE = "derate"
ACTION_EB = "emergency_brake"
ACTION_SHUTDOWN = "shutdown"  # 对齐上游 faultlevel（镜像完整处置域）

#: 等级
LEVEL_INFO = "info"
LEVEL_MINOR = "minor"
LEVEL_MAJOR = "major"
LEVEL_CRITICAL = "critical"

#: faultlevel.FAULTS 的静态镜像（10 键，来自上游 tcms-can-test 实测；改动需同步）
FAULT_LEVELS: dict[str, str] = {
    "soc_low": LEVEL_INFO,
    "temp_high": LEVEL_INFO,
    "door_sensor_noise": LEVEL_MINOR,
    "speed_sensor_drift": LEVEL_MINOR,
    "door_fault": LEVEL_MAJOR,
    "traction_loss": LEVEL_MAJOR,
    "overspeed": LEVEL_MAJOR,
    "eb_failure": LEVEL_CRITICAL,
    "traction_brake_conflict": LEVEL_CRITICAL,
    "pantograph_arc": LEVEL_CRITICAL,
}

#: 等级 → 默认处置（镜像上游 faultlevel.LEVEL_ACTION，实测仅 4 键；
#: shutdown 只存在于 ACTION_PRIORITY 域，10 故障键无 shutdown 级）
LEVEL_ACTION: dict[str, str] = {
    LEVEL_INFO: ACTION_NONE,
    LEVEL_MINOR: ACTION_WARNING,
    LEVEL_MAJOR: ACTION_DERATE,
    LEVEL_CRITICAL: ACTION_EB,
}

#: 故障键 → 中文名（faults.yaml 里取；仅供文案）
FAULT_NAMES: dict[str, str] = {
    "soc_low": "SOC 偏低",
    "temp_high": "电池温度偏高",
    "door_sensor_noise": "车门传感器偶发噪声",
    "speed_sensor_drift": "速度传感器轻微漂移",
    "door_fault": "车门故障",
    "traction_loss": "牵引丢失",
    "overspeed": "超速",
    "eb_failure": "紧急制动执行失败",
    "traction_brake_conflict": "牵引制动冲突",
    "pantograph_arc": "受电弓拉弧风险",
}

#: 故障键 → 涉及的 DBC 信号（供信号断言组合；来自上游 faults.yaml detect 字段）
FAULT_SIGNALS: dict[str, list[tuple[str, str]]] = {
    "soc_low": [("EnergyStatus", "SocPercent")],
    "temp_high": [("EnergyStatus", "BatteryTemp")],
    "door_sensor_noise": [("DoorControl", "Door1State"), ("DoorControl", "AllDoorsClosed")],
    "speed_sensor_drift": [("VehicleSpeed", "SpeedKmh"), ("VehicleSpeed", "SpeedValid")],
    "door_fault": [("DoorControl", "Door2State"), ("DoorControl", "AllDoorsClosed")],
    "traction_loss": [("TractionBrakeHandle", "TractionActive")],
    "overspeed": [("VehicleSpeed", "SpeedKmh")],
    "eb_failure": [("BrakeSystem", "EmergencyBrakeActive")],
    "traction_brake_conflict": [("TractionBrakeHandle", "BrakeActive"), ("TractionBrakeHandle", "TractionActive")],
    "pantograph_arc": [("PantographStatus", "PantographFault"), ("PantographStatus", "LineVoltage")],
}


@dataclass(frozen=True)
class OracleEntry:
    """一个故障键的 oracle 语义（生成期权威期望源）。"""

    key: str
    level: str
    action: str  # 默认处置（mode=auto）
    name: str
    signals: tuple[tuple[str, str], ...] = field(default_factory=tuple)


_ORACLE: dict[str, OracleEntry] = {
    key: OracleEntry(
        key=key,
        level=FAULT_LEVELS[key],
        action=LEVEL_ACTION[FAULT_LEVELS[key]],
        name=FAULT_NAMES[key],
        signals=tuple(FAULT_SIGNALS.get(key, [])),
    )
    for key in FAULT_LEVELS
}


def fault_keys() -> list[str]:
    """白名单故障键（按等级排序）。"""
    order = {LEVEL_INFO: 0, LEVEL_MINOR: 1, LEVEL_MAJOR: 2, LEVEL_CRITICAL: 3}
    return sorted(_ORACLE, key=lambda k: (order[_ORACLE[k].level], k))


def lookup(key: str) -> Optional[OracleEntry]:
    """按故障键查 oracle；未知返回 None（调用方按 parse 失败计）。"""
    return _ORACLE.get(key)


def is_known_fault(key: str) -> bool:
    return key in _ORACLE


def describe(key: str) -> str:
    """面向报告的一句话（key: name（level · action））。"""
    e = _ORACLE.get(key)
    if e is None:
        return f"{key}（未知故障）"
    return f"{e.key}: {e.name}（{e.level} · {e.action}）"


def oracle_summary() -> dict:
    """oracle 汇总（报告/文档用）。"""
    by_level: dict[str, int] = {}
    for e in _ORACLE.values():
        by_level[e.level] = by_level.get(e.level, 0) + 1
    return {
        "total": len(_ORACLE),
        "by_level": dict(sorted(by_level.items())),
        "actions": sorted({e.action for e in _ORACLE.values()}),
        "source": "镜像自 tcms-can-test faultlevel.FAULTS（10 键稳定事实）",
    }


__all__ = [
    "ACTION_DERATE",
    "ACTION_EB",
    "ACTION_NONE",
    "ACTION_WARNING",
    "FAULT_NAMES",
    "FAULT_SIGNALS",
    "OracleEntry",
    "describe",
    "fault_keys",
    "is_known_fault",
    "lookup",
    "oracle_summary",
]
