"""隔离/旁路开关状态机（Isolation & Bypass）—— 对标真实列控的维护旁路管理。

真实轨道交通列控（对标 ETCS/中国 CTCS 的隔离操作）：
    - 设备故障后由维护人员操作**隔离开关**将故障设备从安全链中旁路，
      列车才能继续运行（例如故障 ATP 隔离后转 RM 模式运行）
    - 旁路操作必须满足安全前提（停车、人工确认、记录），且**旁路期间
      安全监视降级**——必须通过驾驶模式限制（如 RM 限速）兜底
    - 旁路状态必须可追溯（谁、何时、为什么旁路），并对安全功能可见

本模块实现：
    - 隔离开关单点状态机：CLOSED(正常) → OPEN(旁路) → CLOSED(恢复)
    - 旁路安全前提校验：速度必须为零（运行中禁止旁路）+
      原因（人工确认，写审计日志）
    - 隔离组（多个开关）：列车级旁路状态聚合 + 允许/禁止条件
    - 旁路期间的安全兜底：任何旁路活动 → 强制降级模式（FAM/CM → RM）
"""

from __future__ import annotations

# 开关状态
SW_CLOSED = "closed"  # 闭合（设备接入安全链，正常工作）
SW_OPEN = "open"  # 打开（设备被旁路，脱离安全链）

# 旁路事件类别
BYPASS_EVENT_OPEN = "bypass_open"
BYPASS_EVENT_CLOSE = "bypass_close"

# 零速阈值（与 EBM 一致）
ZERO_SPEED_THRESHOLD_KMH = 0.5

# 旁路导致的强制降级目标模式
DEGRADED_MODE = "RM"


class IsolationSwitch:
    """单个隔离开关：闭合/打开 + 旁路安全前提 + 审计日志。

    对标真实维护开关：操作必须满足安全前提（停车 + 人工确认），
    每次操作写入审计日志（证据链，谁/何时/为什么）。
    """

    def __init__(
        self, name: str, device: str, zero_speed_threshold_kmh: float = ZERO_SPEED_THRESHOLD_KMH
    ):
        if not name:
            raise ValueError("开关名称不能为空")
        self.name = name
        self.device = device  # 被旁路的设备名（如 "ATP"）
        self.zero_speed_threshold_kmh = zero_speed_threshold_kmh
        self._state = SW_CLOSED
        self._audit: list[dict] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def bypassed(self) -> bool:
        """当前是否处于旁路（打开）状态。"""
        return self._state == SW_OPEN

    @property
    def audit_log(self) -> list[dict]:
        """操作审计日志（深拷贝，不可篡改）。"""
        return [dict(e) for e in self._audit]

    def open_switch(
        self,
        speed_kmh: float = 0.0,
        speed_valid: bool = True,
        operator: str = "maintenance",
        reason: str = "",
    ) -> bool:
        """旁路设备（打开开关）。

        安全前提（对标真实列控维护规程）：
            - 速度必须为零（运行中禁止旁路——旁路即解除安全链）
            - 速度信号必须有效（速度传感器失效时禁止旁路）
        满足前提 → 打开并写审计日志；否则返回 False（不旁路）。
        """
        if not speed_valid or speed_kmh > self.zero_speed_threshold_kmh:
            return False
        if self._state == SW_OPEN:
            return False  # 已旁路：幂等拒绝（避免重复审计）
        self._state = SW_OPEN
        self._audit.append(
            {
                "event": BYPASS_EVENT_OPEN,
                "ts": _monotonic(),
                "operator": operator,
                "reason": reason,
                "speed_kmh": speed_kmh,
            }
        )
        return True

    def close_switch(self, operator: str = "maintenance", reason: str = "") -> bool:
        """恢复设备（闭合开关）。写审计日志。"""
        if self._state == SW_CLOSED:
            return False  # 已闭合：幂等
        self._state = SW_CLOSED
        self._audit.append(
            {
                "event": BYPASS_EVENT_CLOSE,
                "ts": _monotonic(),
                "operator": operator,
                "reason": reason,
            }
        )
        return True

    def reset(self) -> None:
        """维护复位：回到闭合（不写审计，用于测试/初始状态）。"""
        self._state = SW_CLOSED


class IsolationGroup:
    """隔离组：多个开关的列车级聚合。

    任一开关打开（旁路）→ 组处于 degraded（安全链降级）：
    - 强制驾驶模式降级（FAM/CM → RM），用模式限制兜底旁路风险
    - 提供安全前提校验：旁路期间禁止升模式（RM → FAM/CM）
    """

    DEGRADED_MODE = DEGRADED_MODE

    def __init__(self, switches: list[IsolationSwitch]):
        if not switches:
            raise ValueError("隔离组至少需要一个开关")
        self.switches = list(switches)

    @property
    def bypassed_any(self) -> bool:
        """是否有任一开关处于旁路。"""
        return any(s.bypassed for s in self.switches)

    @property
    def bypassed_names(self) -> list[str]:
        """当前被旁路的开关名列表。"""
        return [s.name for s in self.switches if s.bypassed]

    def check_degradation(self, mode: str) -> str:
        """按旁路状态给出当前应处的驾驶模式。

        任一旁路 → 强制 RM（安全兜底）；全部闭合 → 原模式。
        """
        if self.bypassed_any:
            return DEGRADED_MODE
        return mode

    def can_upgrade(self, mode: str) -> bool:
        """旁路期间禁止升模式（RM → FAM/CM）。

        对标真实列控：旁路设备后只能 RM 运行，禁止恢复高级模式
        直至设备修复并闭合开关。
        """
        if self.bypassed_any and mode != DEGRADED_MODE:
            return False
        return True

    def status_report(self) -> dict:
        """组状态报告（供 HMI/维护终端展示）。"""
        return {
            "bypassed_any": self.bypassed_any,
            "bypassed": self.bypassed_names,
            "switch_states": {s.name: s.state for s in self.switches},
            "required_mode": DEGRADED_MODE if self.bypassed_any else None,
        }


def _monotonic() -> float:
    import time

    return time.monotonic()
