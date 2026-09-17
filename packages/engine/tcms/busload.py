"""总线负载率统计与压测（Bus Load）—— 网络级系统指标。

对标 CANoe / CANalyzer 的 Statistics 窗口与整车厂负载率设计规范：
    - 整车厂对 CAN 总线负载率设设计上限（动力/控制 CAN 常见 30~50%，
      诊断/车身 CAN 可放宽），超过上限即为网络容量风险；
    - 负载率是"瞬时值 + 滑动窗口平均值"两个口径（CANoe 同时显示）；
    - 高负载下仲裁延迟增长、低优先级帧丢报/周期劣化——必须可测可断言。

位级帧模型（CAN 2.0A 标准帧，最坏情况位填充）：
    SOF(1) + 仲裁 ID(11) + RTR(1) + IDE(1) + r0(1) + DLC(4) + 数据(8N)
    + CRC(15) + CRCdel(1) + ACK(1) + ACKdel(1) + EOF(7) + IFS(3)
    位填充：SOF~CRC 序列每 5 个连续相同位插入 1 个反向填充位，
    最坏情况填充位数 = floor((L-1)/4)（L=可填充区段位数）。

锚点验证（业界公认值）：
    - 0 字节数据帧：34 可填充位 + 8 填充 = 55 位（最坏）
    - 8 字节数据帧：98 可填充位 + 24 填充 = 135 位（最坏）

仲裁优先级：ID 越小优先级越高——低 ID 帧的 WCRT 受高优先级流量挤压，
这是"负载率升高 → 低优先级帧劣化"的物理机制（配合 schedulability.py）。
"""

from __future__ import annotations

from collections import deque

# 帧结构位域长度（CAN 2.0A 标准帧，可填充区段 + 固定尾段）
STUFFABLE_FIXED_BITS = 1 + 11 + 1 + 1 + 1 + 4 + 15  # SOF+ID+RTR+IDE+r0+DLC+CRC
TRAILER_BITS = 1 + 1 + 1 + 7 + 3  # CRCdel+ACK+ACKdel+EOF+IFS
BITS_PER_BYTE = 8

# 行业惯例负载率设计上限（可被调用方覆盖）
DESIGN_LIMIT_PCT = 50.0  # 控制 CAN 典型设计上限
WARNING_PCT = 30.0  # 预警线


def frame_bits(dlc: int) -> int:
    """单帧总位数（含最坏情况位填充）。

    dlc 必须 0..8；位填充按最坏情况 floor((L-1)/4) 计算（L=可填充位数），
    这是"上界"口径——与 Tindell 分析中的 C（传输时间）取上界一致。
    """
    if not 0 <= dlc <= 8:
        raise ValueError(f"dlc 必须 0..8，got {dlc}")
    stuffable = STUFFABLE_FIXED_BITS + BITS_PER_BYTE * dlc
    stuffing = max(0, (stuffable - 1) // 4)
    return stuffable + stuffing + TRAILER_BITS


def frame_time_s(dlc: int, bitrate: int) -> float:
    """单帧占用时间（秒）。"""
    if bitrate <= 0:
        raise ValueError(f"bitrate 必须为正数，got {bitrate}")
    return frame_bits(dlc) / bitrate


class BusLoadMonitor:
    """滑动窗口总线负载率监视器：逐帧喂入、即时输出瞬时/平均负载。

    负载率定义：窗口内所有帧的位时间之和 / 窗口时长。
    位时间按最坏填充位计算（上界口径，与可调度性分析一致）。
    """

    def __init__(self, bitrate: int = 250_000, window_s: float = 1.0):
        if bitrate <= 0:
            raise ValueError(f"bitrate 必须为正数，got {bitrate}")
        if window_s <= 0:
            raise ValueError(f"window_s 必须为正数，got {window_s}")
        self.bitrate = bitrate
        self.window_s = window_s
        self._frames: deque[tuple[float, float]] = deque()  # (ts, bits)
        self._total_frames = 0
        self._start_ts: float | None = None  # 首帧时间戳（窗口下界锚点）

    def on_frame(self, dlc: int, ts: float) -> None:
        """记录一帧：刷新滑动窗口（丢弃窗口外的旧帧）。"""
        if self._start_ts is None:
            self._start_ts = ts
        self._frames.append((ts, frame_bits(dlc)))
        self._total_frames += 1
        self._slide(ts)

    def _slide(self, ts: float) -> None:
        while self._frames and ts - self._frames[0][0] > self.window_s:
            self._frames.popleft()

    def load_pct(self, ts: float) -> float:
        """当前窗口平均负载率（%）：窗口内帧位时间总和 / 有效窗口时长。"""
        self._slide(ts)
        if not self._frames or self._start_ts is None:
            return 0.0
        window_start = max(ts - self.window_s, self._start_ts)
        effective = ts - window_start
        if effective <= 0:
            return 0.0
        bits = sum(b for t, b in self._frames if t >= window_start)
        return 100.0 * bits / (effective * self.bitrate)

    @property
    def total_frames(self) -> int:
        return self._total_frames

    def assess(
        self,
        ts: float,
        design_limit_pct: float = DESIGN_LIMIT_PCT,
        warning_pct: float = WARNING_PCT,
    ) -> dict:
        """负载评估：与设计上限/预警线对比，输出网络健康结论。"""
        load = self.load_pct(ts)
        if load > design_limit_pct:
            level = "over_limit"
        elif load > warning_pct:
            level = "warning"
        else:
            level = "healthy"
        return {
            "load_pct": load,
            "level": level,
            "design_limit_pct": design_limit_pct,
            "warning_pct": warning_pct,
        }


class BusLoadGenerator:
    """背景流量规划器：规划周期流清单把总线压到目标负载率。

    用于压力场景构建（配合可调度性分析断言低优先级帧劣化）：
    输出 (arb_id, dlc, period_s) 流清单，其理论总负载 = target_pct。
    """

    def __init__(self, bitrate: int = 250_000):
        if bitrate <= 0:
            raise ValueError(f"bitrate 必须为正数，got {bitrate}")
        self.bitrate = bitrate

    def expected_load_pct(self, streams: list[dict]) -> float:
        """流清单叠加后的理论平均负载率（%）。"""
        total = sum(frame_bits(s["dlc"]) / s["period_s"] for s in streams)
        return 100.0 * total / self.bitrate

    def plan_streams_for_target(
        self,
        target_pct: float,
        low_prio_base_id: int = 0x500,
        dlc: int = 8,
        min_period_s: float = 0.005,
        max_period_s: float = 0.1,
        max_streams: int = 500,
    ) -> list[dict]:
        """规划背景流清单，使理论总负载接近 target_pct。

        策略：先在最短周期档位铺流（每档位可容纳 ⌊负载预算/单流速率⌋ 条），
        档位装满后周期翻倍进入下一档，直至预算用尽。
        返回 [{arb_id, dlc, period_s}, ...]（arb_id 依次 +0x10，互不冲突）。
        """
        if not 0 < target_pct < 100:
            raise ValueError(f"target_pct 必须 (0,100)，got {target_pct}")
        budget = target_pct / 100.0 * self.bitrate  # 目标总位速率（bit/s）
        streams: list[dict] = []
        used = 0.0
        base = low_prio_base_id
        period = min_period_s
        while used < budget and period <= max_period_s and len(streams) < max_streams:
            rate = frame_bits(dlc) / period
            if rate <= 0:
                break
            while used + rate <= budget and len(streams) < max_streams:
                streams.append({"arb_id": base, "dlc": dlc, "period_s": period})
                used += rate
                base += 0x10
            period *= 2
        return streams
