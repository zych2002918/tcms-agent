"""紧急制动管理（EBM）：驾驶模式 × 制动原因 × 处置矩阵 + 缓解/复位闭环。

对标真实 TCMS 的"紧急制动原因表"：同一原因在不同驾驶模式下处置不同，
紧急制动必须按 模式 × 原因 查表决策，并完成"触发 → 缓解 → 复位"闭环。

设计决策（面试亮点）：
- 非法组合（原因不适用于当前驾驶模式）→ 只记录提示，不误制动；
- SIL4 紧急制动：双通道"任一触发即制动"（故障安全——宁可错杀，不可漏放）；
- SIL2 原因：双通道"一致才制动"（防误报——避免偶发噪声导致无谓紧急制动）；
- 自愈复位限 1 次，超限必须远程/人工复位——对标真实系统的恢复策略。
"""

MODE_FAM = "FAM"  # 全自动（ATO 驾驶）
MODE_CM = "CM"  # 受控人工（ATP 防护下人工驾驶）
MODE_RM = "RM"  # 限制人工（低限速人工驾驶）

VALID_MODES = (MODE_FAM, MODE_CM, MODE_RM)
DEGRADATION_CHAIN = (MODE_FAM, MODE_CM, MODE_RM)  # 降级链：FAM→CM→RM

STATE_IDLE = "IDLE"
STATE_BRAKE = "BRAKE"
STATE_RELEASED = "RELEASED"
STATE_FAULT = "FAULT"
# 司机缓解操作序列的中间态（对标真实 EB 缓解：手柄回零 + 缓解按钮保持）
STATE_WAIT_HANDLE_ZERO = "WAIT_HANDLE_ZERO"  # 已停车，等待司机手柄回零
STATE_WAIT_RELEASE_BTN = "WAIT_RELEASE_BTN"  # 手柄已回零，等待缓解按钮保持
VALID_STATES = (
    STATE_IDLE,
    STATE_BRAKE,
    STATE_RELEASED,
    STATE_FAULT,
    STATE_WAIT_HANDLE_ZERO,
    STATE_WAIT_RELEASE_BTN,
)

MAX_SELF_HEAL = 1  # 自愈复位次数上限（之后需远程/人工复位）
RELEASE_HOLD_S = 3.0  # 司机缓解按钮需保持的最短时长（秒）

# 制动原因表（对标真实 25+ 原因中的典型子集）：
#   每种: (适用模式, 处置动作, SIL 等级)
#   action 两种形式: "emergency_brake" / "emergency_brake+mode_<目标模式>"
REASONS = {
    "overspeed": {
        "modes": (MODE_FAM, MODE_CM, MODE_RM),
        "action": "emergency_brake",
        "sil": 4,
        "desc": "超速（超过允许限速）",
    },
    "door_open": {
        "modes": (MODE_FAM, MODE_CM),
        "action": "emergency_brake",
        "sil": 4,
        "desc": "运行中车门打开",
    },
    "ato_fault": {
        "modes": (MODE_FAM,),
        "action": "emergency_brake+mode_cm",
        "sil": 2,
        "desc": "ATO 故障 → 降级 CM",
    },
    "atp_fault": {
        "modes": (MODE_FAM, MODE_CM),
        "action": "emergency_brake+mode_rm",
        "sil": 4,
        "desc": "ATP 故障 → 降级 RM",
    },
    "obstacle": {
        "modes": (MODE_FAM, MODE_CM),
        "action": "emergency_brake",
        "sil": 4,
        "desc": "检测到障碍物",
    },
    "fire_alarm": {
        "modes": (MODE_FAM, MODE_CM, MODE_RM),
        "action": "emergency_brake",
        "sil": 2,
        "desc": "火灾报警",
    },
    "maintenance_sw": {
        "modes": (MODE_FAM, MODE_CM, MODE_RM),
        "action": "emergency_brake",
        "sil": 4,
        "desc": "维护开关动作",
    },
    "hardwire_loss": {
        "modes": (MODE_FAM, MODE_CM, MODE_RM),
        "action": "emergency_brake",
        "sil": 4,
        "desc": "CAN 网络故障 → 硬线备份",
    },
}


def action_parts(action: str) -> tuple[bool, str | None]:
    """解析处置动作。

    "emergency_brake"            → (True, None)
    "emergency_brake+mode_cm"    → (True, "CM")
    """
    parts = action.split("+")
    brake = "emergency_brake" in parts
    mode_change = None
    for part in parts[1:]:
        if part.startswith("mode_"):
            mode_change = part[len("mode_") :].upper()
    return brake, mode_change


class EmergencyBrakeManager:
    """紧急制动管理器：模式×原因矩阵决策 + 缓解/复位闭环状态机。

    状态迁移：
        IDLE ──trigger(适用原因)──▶ BRAKE ──零速且原因消失──▶ RELEASED ──reset──▶ IDLE
        BRAKE ──self_heal(次数内)──▶ IDLE
        self_heal 超限 ──▶ FAULT ──远程 reset──▶ IDLE
        trigger(不适用原因)：不迁移状态，仅记录提示
    """

    def __init__(self, mode: str = MODE_FAM, zero_speed_threshold_kmh: float = 0.5):
        if mode not in VALID_MODES:
            raise ValueError(f"未知驾驶模式: {mode}（合法值 {VALID_MODES}）")
        self._mode = mode
        self.zero_speed_threshold_kmh = zero_speed_threshold_kmh  # 零速判定阈值（km/h）
        self._state = STATE_IDLE
        self._active_reasons: dict[str, bool] = {}  # 原因 -> 是否仍存在（外部喂入）
        self._self_heal_used = 0
        self._records: list[dict] = []  # 全部触发/提示记录（含不适用原因）
        self._vote_mismatches = 0  # SIL2 表决通道不一致计数（通道失效诊断）

    # ---- 只读属性 ----

    @property
    def mode(self) -> str:
        """当前驾驶模式。"""
        return self._mode

    @property
    def state(self) -> str:
        """当前状态：IDLE / BRAKE / RELEASED / FAULT。"""
        return self._state

    @property
    def records(self) -> list[dict]:
        """触发/提示记录（含不适用原因），便于缺陷定位与审计。"""
        return list(self._records)

    @property
    def self_heal_used(self) -> int:
        """已使用的自愈次数（上限 MAX_SELF_HEAL=1）。"""
        return self._self_heal_used

    # ---- 模式管理 ----

    def set_mode(self, mode: str) -> None:
        """人工/系统设定驾驶模式。

        模式迁移只允许沿降级链移动一步（FAM↔CM↔RM），
        不允许跳级（如 RM→FAM 直接恢复全自动）。
        """
        if mode not in VALID_MODES:
            raise ValueError(f"未知驾驶模式: {mode}（合法值 {VALID_MODES}）")
        if mode == self._mode:
            return
        cur = DEGRADATION_CHAIN.index(self._mode)
        nxt = DEGRADATION_CHAIN.index(mode)
        if abs(nxt - cur) != 1:
            raise ValueError(f"非法模式跳级: {self._mode} -> {mode}（仅允许单步迁移）")
        self._mode = mode

    # ---- 触发决策 ----

    def trigger(self, reason: str) -> dict:
        """按 模式×原因 矩阵判定一次紧急制动触发。

        返回: {"reason", "applied", "action", "mode_change", "sil"}
        - 原因适用本模式：紧急制动（必要时同时按矩阵降级驾驶模式）→ BRAKE
        - 原因不适用本模式：只记录提示（record_only），不误制动
        """
        if reason not in REASONS:
            raise ValueError(f"未知制动原因: {reason}")
        cfg = REASONS[reason]
        if self._mode not in cfg["modes"]:
            record = {
                "reason": reason,
                "mode": self._mode,
                "applied": False,
                "action": "record_only",
                "mode_change": None,
                "sil": cfg["sil"],
            }
            self._records.append(record)
            return record

        brake, mode_change = action_parts(cfg["action"])
        if brake:
            self._state = STATE_BRAKE
        # 故障驱动的模式降级由系统强制（如 ATO 故障 FAM→CM、ATP 故障 →RM），
        # 属安全降级链，不受人工单步迁移限制
        if mode_change:
            self._mode = mode_change
        record = {
            "reason": reason,
            "mode": mode_change or self._mode,
            "applied": True,
            "action": cfg["action"],
            "mode_change": mode_change,
            "sil": cfg["sil"],
        }
        self._records.append(record)
        return record

    # ---- 缓解闭环 ----

    def update_reason_status(self, reason: str, active: bool) -> None:
        """外部喂入原因状态（故障是否已消失），供缓解条件判断。"""
        if reason not in REASONS:
            raise ValueError(f"未知制动原因: {reason}")
        self._active_reasons[reason] = bool(active)

    def release_condition(self, speed_kmh: float, speed_valid: bool = True) -> bool:
        """缓解评估：零速（<= 阈值）且全部已触发原因消失。

        条件满足且当前处于 BRAKE 时，状态迁移至 RELEASED；
        条件不满足（速度未归零 / 原因仍在 / 速度信号无效）保持 BRAKE。
        speed_valid=False（速度传感器失效）时**禁止缓解**——速度按 0 喂入
        不构成缓解依据（对标真实 EBR：缓解必须依赖有效速度信号）。
        """
        at_zero = speed_valid and speed_kmh <= self.zero_speed_threshold_kmh
        cleared = not any(active for active in self._active_reasons.values())
        if self._state == STATE_BRAKE and at_zero and cleared:
            self._state = STATE_RELEASED
        return bool(at_zero and cleared)

    def _release_prereq(self, speed_kmh: float, speed_valid: bool) -> bool:
        """复位/自愈的安全前提：速度有效且零速 + 全部原因真实消失。"""
        return (
            speed_valid
            and speed_kmh <= self.zero_speed_threshold_kmh
            and not any(self._active_reasons.values())
        )

    # ---- 复位 ----

    def self_heal(self, speed_kmh: float | None = None, speed_valid: bool = True) -> bool:
        """自愈复位：限 1 次，成功后恢复 IDLE；超限转入 FAULT（需远程复位）。

        给定 speed_kmh 时执行"运行中禁止自动解除制动"校验
        （零速且原因消失才允许自愈）；不传则视为系统确认停车场景（强自愈）。
        """
        if speed_kmh is not None and not self._release_prereq(speed_kmh, speed_valid):
            return False  # 运行中 / 速度信号失效：拒绝自动解除紧急制动
        if self._self_heal_used >= MAX_SELF_HEAL:
            self._state = STATE_FAULT
            return False
        self._self_heal_used += 1
        for reason in self._active_reasons:
            self._active_reasons[reason] = False  # 自愈视为故障源恢复
        self._state = STATE_IDLE
        return True

    def reset(self, speed_kmh: float | None = None, speed_valid: bool = True) -> None:
        """远程/人工复位：清除全部原因、恢复自愈能力、回到 IDLE。

        给定 speed_kmh 时校验复位安全前提（零速且原因消失），
        不满足抛 ValueError——运行中复位等于运行中解除紧急制动，必须拒绝。
        """
        if speed_kmh is not None and not self._release_prereq(speed_kmh, speed_valid):
            raise ValueError("远程复位安全前提不满足：需零速且全部制动原因消失")
        for reason in self._active_reasons:
            self._active_reasons[reason] = False
        self._self_heal_used = 0
        self._state = STATE_IDLE

    # ---- 司机缓解操作序列（对标真实 EB 缓解的两步操作） ----

    def prepare_release(
        self, handle_position: int, speed_kmh: float | None = None, speed_valid: bool = True
    ) -> bool:
        """第一步：停车后司机手柄回零（EB 位退回缓解位）。

        前提：处于 BRAKE、零速（给定速度时校验）、全部原因消失。
        手柄位置必须为 0；成功后进入 WAIT_HANDLE_ZERO。
        返回 False 表示操作无效（未制动/运行中/原因仍在/手柄未回零）。
        """
        if self._state != STATE_BRAKE:
            return False
        if speed_kmh is not None and not self._release_prereq(speed_kmh, speed_valid):
            return False
        if any(self._active_reasons.values()):
            return False  # 原因仍在 → 缓解序列禁止启动
        if handle_position != 0:
            return False  # 手柄未回零 → 拒绝（真实 EB 缓解第一步就是回零）
        self._state = STATE_WAIT_HANDLE_ZERO
        return True

    def hold_release_button(self, seconds: float) -> bool:
        """第二步：司机按下缓解按钮并保持 ≥ RELEASE_HOLD_S 秒。

        成功后 EBR 回路重新得电，制动缓解，状态回 IDLE。
        保持不足 → 进入 WAIT_RELEASE_BTN（可再次尝试）；
        状态不对（未完成手柄回零）→ 返回 False。
        """
        if self._state not in (STATE_WAIT_HANDLE_ZERO, STATE_WAIT_RELEASE_BTN):
            return False
        if seconds < RELEASE_HOLD_S:
            self._state = STATE_WAIT_RELEASE_BTN  # 按下过但保持不足，可重试
            return False
        self._state = STATE_IDLE
        return True

    # ---- SIL 双通道表决 ----

    def safety_verification(self, reason: str) -> bool:
        """SIL 标注：SIL4 原因要求双通道一致验证，返回是否需要。"""
        if reason not in REASONS:
            raise ValueError(f"未知制动原因: {reason}")
        return REASONS[reason]["sil"] >= 4

    def channel_vote(self, reason: str, channel_a: bool, channel_b: bool) -> bool:
        """双通道表决：两路独立信号是否触发该原因。

        设计决策（面试要点）：
        - SIL4（如超速、ATP 故障）：任一通道触发即制动——故障安全，
          制动的失效代价远高于误制动，宁可错杀不可漏放；
        - SIL2（如 ATO 故障、火灾报警）：双通道一致才制动——防误报，
          避免传感器偶发噪声导致无谓紧急制动；两通道不一致时累计
          通道失效诊断计数（vote_mismatches），供维护定位传感器故障。
        """
        if reason not in REASONS:
            raise ValueError(f"未知制动原因: {reason}")
        if REASONS[reason]["sil"] >= 4:
            return channel_a or channel_b
        if channel_a != channel_b:
            self._vote_mismatches += 1  # 2oo2 表决不一致：记录通道失效迹象
        return channel_a and channel_b

    @property
    def vote_mismatches(self) -> int:
        """SIL2 双通道表决不一致累计次数（通道失效诊断指标）。"""
        return self._vote_mismatches
