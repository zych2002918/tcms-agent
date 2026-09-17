"""报文序列与时序违规检测（Sequence & Timing Violation Check）—— 对标协议一致性测试。

真实 CAN 协议测试（ISO 16845 一致性测试思路）不只验证单帧内容，
还要验证**报文流**的时序/序列行为：
    - 丢帧（missing）：期望周期内未收到某帧（超时）
    - 重复帧（duplicate）：同一序列号/内容在周期内出现多次
    - 乱序帧（out-of-order）：计数器回绕外的序号跳变/回退
    - 迟到帧（late）：到达间隔超过容忍阈值

本模块提供流式检测器：喂入 (arb_id, seq, timestamp) 事件，
实时判定违规类型并计数。
"""

# 违规类型
VIOLATION_MISSING = "missing_frame"  # 丢帧（超时未到）
VIOLATION_DUPLICATE = "duplicate_frame"  # 重复帧（同序号重复）
VIOLATION_OUT_OF_ORDER = "out_of_order"  # 乱序帧（序号跳变/回退）
VIOLATION_LATE = "late_frame"  # 迟到帧（间隔超容忍）

# 默认参数
DEFAULT_TIMEOUT_S = 0.3  # 期望周期 100ms 的 3 倍判超时
DEFAULT_SEQ_MOD = 256  # 心跳计数器回绕模数
DEFAULT_TOLERANCE_RATIO = 0.2  # 迟到容忍：周期 ±20%


class SequenceChecker:
    """报文序列/时序违规检测器（流式）。

    用法：
        sc = SequenceChecker(period_s=0.1)
        sc.on_frame(0x100, 5, 0.0)   # (arb_id, seq, ts)
        sc.on_frame(0x100, 6, 0.1)
        sc.violations   # 违规计数
        sc.last_violation
    """

    def __init__(
        self,
        period_s: float = 0.1,
        timeout_s: float | None = None,
        seq_mod: int = DEFAULT_SEQ_MOD,
        tolerance_ratio: float = DEFAULT_TOLERANCE_RATIO,
    ):
        self.period = period_s
        self.timeout = timeout_s or period_s * 3
        self.seq_mod = seq_mod
        self.tolerance = tolerance_ratio
        self.violations: dict[str, int] = {
            VIOLATION_MISSING: 0,
            VIOLATION_DUPLICATE: 0,
            VIOLATION_OUT_OF_ORDER: 0,
            VIOLATION_LATE: 0,
        }
        self.last_violation: str | None = None
        self._last_seq: dict[int, int] = {}  # arb_id -> 上一序号
        self._last_ts: dict[int, float] = {}  # arb_id -> 上一时间戳
        self._total: dict[int, int] = {}  # arb_id -> 收到帧数

    def _record(self, kind: str) -> None:
        self.violations[kind] += 1
        self.last_violation = kind

    def on_frame(self, arb_id: int, seq: int, timestamp_s: float) -> None:
        """喂入一帧。按 (arb_id, seq, ts) 判定序列/时序违规。"""
        is_duplicate = False
        # 重复：与上一帧相同序号且间隔小于周期（同周期重复发送）
        if arb_id in self._last_seq and seq == self._last_seq[arb_id]:
            interval = timestamp_s - self._last_ts.get(arb_id, timestamp_s)
            if interval < self.period * (1 - self.tolerance):
                self._record(VIOLATION_DUPLICATE)
                is_duplicate = True

        # 乱序：非重复帧且序号 != (last+1) % mod（允许回绕）
        if not is_duplicate and arb_id in self._last_seq:
            expected = (self._last_seq[arb_id] + 1) % self.seq_mod
            if seq != expected:
                self._record(VIOLATION_OUT_OF_ORDER)

        # 迟到：间隔超过容忍上限
        if arb_id in self._last_ts:
            interval = timestamp_s - self._last_ts[arb_id]
            if interval > self.period * (1 + self.tolerance):
                self._record(VIOLATION_LATE)

        self._last_seq[arb_id] = seq
        self._last_ts[arb_id] = timestamp_s
        self._total[arb_id] = self._total.get(arb_id, 0) + 1

    def check_timeout(self, arb_id: int, timestamp_s: float) -> None:
        """显式超时检查：距上一帧超过 timeout 判丢帧。"""
        if arb_id in self._last_ts and timestamp_s - self._last_ts[arb_id] > self.timeout:
            self._record(VIOLATION_MISSING)

    def reset(self) -> None:
        """清空状态（新观察窗口）。"""
        self.violations = {k: 0 for k in self.violations}
        self.last_violation = None
        self._last_seq.clear()
        self._last_ts.clear()
        self._total.clear()

    def total_frames(self, arb_id: int | None = None) -> int:
        """收到帧数（按 ID 或总计）。"""
        if arb_id is not None:
            return self._total.get(arb_id, 0)
        return sum(self._total.values())
