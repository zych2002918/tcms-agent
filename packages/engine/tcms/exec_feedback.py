"""EB 执行反馈闭环（Execution Feedback）—— 紧急制动"决策"到"执行"的可信验证。

对标真实 TCMS 的"制动实施监督"：EBM 发出紧急制动请求只是决策，制动是否
真正施加、牵引是否真正切除，必须由执行层反馈闭环确认——否则"决策正确"
无法推导出"列车已安全停车"。三条反馈证据交叉校验：

    1. 制动缸压力（DBC: BrakeSystem.BrakeCylinderPressure，0.1 kPa/LSB）：
       请求后在时限内须达到施加阈值，否则制动施加失败；
    2. 紧急制动激活标志（BrakeSystem.EmergencyBrakeActive）：BCU 的确认回执；
    3. 牵引切除联锁：制动执行期间牵引必须保持切除——执行期间牵引恢复
       = 最危险的执行层失效（边制动边牵引），立即判定联锁故障。

设计决策（面试亮点）：
- 决策/执行分离：EBM 负责"该不该制动"，本模块负责"制动有没有生效"，
  两层各自的故障模式不同（决策失效 vs 执行失效），必须分别检测；
- 三重证据交叉校验：压力 + 回执 + 联锁，任一缺失/违背即 FAULT——
  单传感器可信度有限，多源一致才构成执行确认；
- 联锁违背（APPLIED 期间牵引恢复）比反馈超时更严重：立即 FAULT，
  不等超时窗口——执行层失效的直接证据优先；
- 时间单调性校验：拒绝时间倒流的反馈样本（对抗乱序/重放输入）；
- 缓解同样需要反馈：压力须回落到释放阈值以下才算真正缓解完成。
"""

FEEDBACK_TIMEOUT_S = 2.0  # EB 请求后等待执行反馈的最长时限（秒）
PRESSURE_APPLIED_KPA = 300.0  # 判定制动已施加的制动缸压力阈值（kPa）
PRESSURE_RELEASED_KPA = 50.0  # 判定制动已缓解的压力上限（kPa）

STATE_IDLE = "IDLE"
STATE_PENDING = "PENDING"  # EB 已请求，等待执行反馈
STATE_APPLIED = "APPLIED"  # 压力到位 + 回执 + 牵引切除，执行确认
STATE_FEEDBACK_FAULT = "FEEDBACK_FAULT"  # 超时/联锁违背/回执缺失：执行层失效
VALID_STATES = (STATE_IDLE, STATE_PENDING, STATE_APPLIED, STATE_FEEDBACK_FAULT)


class EbExecutionFeedback:
    """紧急制动执行反馈监视器：验证 EB 请求是否被真实执行。

    事件驱动模型（外部按周期喂入真实信号）：
        IDLE ──request_eb──▶ PENDING ──压力达标+回执+牵引切除──▶ APPLIED
        PENDING ──evaluate 超时──▶ FEEDBACK_FAULT
        PENDING ──压力达标但牵引未切除/回执缺失──▶ FEEDBACK_FAULT
        APPLIED ──on_traction(active=True)──▶ FEEDBACK_FAULT（联锁违背）
        APPLIED ──压力回落至释放阈值──▶ IDLE（缓解完成）
        FEEDBACK_FAULT ──reset──▶ IDLE

    时间源：调用方传入单调时钟（time.monotonic 或注入的测试时钟），
    模块自身不读墙钟——保证 CI 可复现、时间可注入。
    """

    def __init__(
        self,
        ebm_manager=None,
        timeout_s: float = FEEDBACK_TIMEOUT_S,
        applied_kpa: float = PRESSURE_APPLIED_KPA,
        released_kpa: float = PRESSURE_RELEASED_KPA,
    ):
        if timeout_s <= 0:
            raise ValueError(f"timeout_s 必须为正数，got {timeout_s}")
        if not (released_kpa < applied_kpa):
            raise ValueError(f"released_kpa({released_kpa}) 必须小于 applied_kpa({applied_kpa})")
        self._ebm = ebm_manager
        self.timeout_s = timeout_s
        self.applied_kpa = applied_kpa
        self.released_kpa = released_kpa
        self._state = STATE_IDLE
        self._request: dict | None = None  # {"reason", "ts", ...}
        self._last_ts: float | None = None  # 单调时间校验
        self._records: list[dict] = []

    # ---- 只读属性 ----

    @property
    def state(self) -> str:
        return self._state

    @property
    def pending_request(self) -> dict | None:
        """当前挂起的 EB 执行请求（深拷贝）。"""
        import copy

        return copy.deepcopy(self._request)

    @property
    def records(self) -> list[dict]:
        import copy

        return copy.deepcopy(self._records)

    # ---- 请求 ----

    def request_eb(self, reason: str, ts: float) -> bool:
        """EBM 决策后发起执行请求：进入 PENDING，等待执行反馈。

        新请求覆盖旧挂起请求（同一次制动只跟踪最新决策）；
        时间单调性校验失败拒绝请求。
        """
        if not self._accept_ts(ts):
            return False
        self._request = {"reason": reason, "ts": ts}
        self._state = STATE_PENDING
        self._record("request", reason=reason, ts=ts)
        return True

    # ---- 反馈输入 ----

    def on_pressure(self, kpa: float, ts: float) -> bool:
        """喂入制动缸压力（kPa）。

        PENDING 且压力达施加阈值：继续校验回执与联锁后确认执行；
        APPLIED 且压力回落至释放阈值：确认缓解完成 → IDLE。
        """
        if not self._accept_ts(ts):
            return False
        if self._state == STATE_PENDING:
            if self._request is not None and ts - self._request["ts"] > self.timeout_s:
                self._fail("feedback_timeout", ts=ts, kpa=kpa)
                return False
            if kpa >= self.applied_kpa:
                # 压力到位：等待 eb_active 回执 + 牵引切除确认后才算 APPLIED
                self._request["pressure_ok"] = True
                self._request["pressure_ts"] = ts
                self._record("pressure_reached", kpa=kpa, ts=ts)
                self._try_confirm(ts, kpa)
                return True
        elif self._state == STATE_APPLIED:
            if kpa <= self.released_kpa:
                self._state = STATE_IDLE
                self._record("released", kpa=kpa, ts=ts)
                self._request = None
                return True
        return False

    def on_eb_active(self, active: bool, ts: float) -> None:
        """喂入 EmergencyBrakeActive 回执。

        PENDING 中回执置位：标记并尝试确认；
        回执缺失视为无确认（在 evaluate 超时兜底判 FAULT）。
        """
        if not self._accept_ts(ts):
            return
        if self._state == STATE_PENDING and active:
            self._request["eb_active"] = True
            self._record("eb_active_ack", ts=ts)
            self._try_confirm(ts, None)

    def on_traction(self, active: bool, ts: float) -> None:
        """喂入牵引状态。

        APPLIED 期间牵引恢复 = 联锁违背（边制动边牵引）→ 立即 FAULT；
        PENDING 中牵引已切除：标记并尝试确认。
        """
        if not self._accept_ts(ts):
            return
        if self._state == STATE_APPLIED and active:
            self._fail("interlock_violation", ts=ts)
            return
        if self._state == STATE_PENDING and not active:
            self._request["traction_off"] = True
            self._record("traction_off", ts=ts)
            self._try_confirm(ts, None)

    def evaluate(self, ts: float) -> bool:
        """周期自检：PENDING 超过时限仍未确认 → FEEDBACK_FAULT。

        返回当前状态是否健康（非 FAULT）。
        """
        if not self._accept_ts(ts):
            return self._state != STATE_FEEDBACK_FAULT
        if (
            self._state == STATE_PENDING
            and self._request is not None
            and ts - self._request["ts"] > self.timeout_s
        ):
            missing = []
            if not self._request.get("pressure_ok"):
                missing.append("pressure")
            if not self._request.get("eb_active"):
                missing.append("eb_active")
            if not self._request.get("traction_off"):
                missing.append("traction_off")
            self._fail("feedback_timeout", ts=ts, missing=missing)
        return self._state != STATE_FEEDBACK_FAULT

    # ---- 复位 ----

    def reset(self) -> None:
        """维护复位：FEEDBACK_FAULT → IDLE（保留审计记录）。"""
        self._state = STATE_IDLE
        self._request = None
        self._record("reset")

    # ---- 内部 ----

    def _try_confirm(self, ts: float, kpa: float | None) -> None:
        """三重证据齐备（压力+回执+牵引切除）→ APPLIED。"""
        if self._state != STATE_PENDING or self._request is None:
            return
        r = self._request
        if r.get("pressure_ok") and r.get("eb_active") and r.get("traction_off"):
            self._state = STATE_APPLIED
            self._record("applied", ts=ts, kpa=kpa)

    def _fail(self, cause: str, ts: float, **extra) -> None:
        if self._state == STATE_FEEDBACK_FAULT:
            return
        self._state = STATE_FEEDBACK_FAULT
        self._record("fault", cause=cause, ts=ts, **extra)

    def _accept_ts(self, ts: float) -> bool:
        """时间单调性校验：拒绝时间倒流样本（乱序/重放防护）。"""
        if self._last_ts is not None and ts < self._last_ts:
            self._record("rejected_timestamp", ts=ts)
            return False
        self._last_ts = ts
        return True

    def _record(self, event: str, **extra) -> None:
        rec = {"event": event, "state": self._state, **extra}
        self._records.append(rec)
