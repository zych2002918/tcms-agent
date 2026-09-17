"""周期抖动与漂移统计（Jitter / Drift Statistics）—— 对标真实总线时序质量。

真实 CAN 网络中，节点时钟漂移（ppm）与调度抖动（jitter）导致帧到达
间隔偏离标称周期。时序质量指标：
    - 抖动（jitter）：帧间隔相对标称周期的偏差（min/max/mean/σ）
    - 漂移（drift）：长期时钟偏差，用 ppm（百万分之一）衡量
    - 超时/迟到：间隔超过容忍阈值（如标称周期的 ±20%）的事件计数

用途：
    - 测试框架里验证"抖动注入是否被正确统计"
    - 模拟真实总线"偶尔抖动但可容忍" vs "严重漂移需告警"的区分
"""

import statistics

# 标称周期容忍范围（相对标称周期的比例）
JITTER_TOLERANCE_RATIO = 0.2  # 间隔偏差 ≤ 标称周期 20% 视为正常
DRIFT_WARN_PPM = 200.0  # 漂移超过 200ppm 告警（真实 TCMS 时钟典型 ±100ppm）


class JitterMonitor:
    """周期抖动/漂移监视器：喂入帧时间戳，输出时序质量统计。

    用法：
        jm = JitterMonitor(nominal_period_s=0.1)
        jm.observe(0.00); jm.observe(0.10); jm.observe(0.19)  # 间隔 0.10/0.09
        jm.stats()      # {'count': 2, 'mean': 0.095, ...}
        jm.drift_ppm()  # 相对标称周期的长期漂移
    """

    def __init__(
        self, nominal_period_s: float = 0.1, tolerance_ratio: float = JITTER_TOLERANCE_RATIO
    ):
        self.nominal = nominal_period_s
        self.tolerance = tolerance_ratio
        self._intervals: list[float] = []
        self._last_ts: float | None = None
        self._late_events: int = 0

    def observe(self, timestamp_s: float) -> None:
        """记录一帧到达时间（秒）。首帧不产生间隔。"""
        if self._last_ts is not None:
            interval = timestamp_s - self._last_ts
            if interval < 0:
                raise ValueError(f"时间戳回退: {timestamp_s} < {self._last_ts}")
            self._intervals.append(interval)
            if interval > self.nominal * (1 + self.tolerance):
                self._late_events += 1
        self._last_ts = timestamp_s

    @property
    def late_events(self) -> int:
        """超过容忍上限的间隔数（迟到事件）。"""
        return self._late_events

    def stats(self) -> dict:
        """间隔统计：count / min / max / mean / stdev。"""
        if not self._intervals:
            return {"count": 0, "min": None, "max": None, "mean": None, "stdev": None}
        return {
            "count": len(self._intervals),
            "min": min(self._intervals),
            "max": max(self._intervals),
            "mean": statistics.fmean(self._intervals),
            "stdev": statistics.stdev(self._intervals) if len(self._intervals) > 1 else 0.0,
        }

    def drift_ppm(self) -> float:
        """长期漂移（ppm）：(平均间隔 - 标称) / 标称 * 1e6。"""
        s = self.stats()
        if s["count"] == 0 or s["mean"] is None:
            return 0.0
        return (s["mean"] - self.nominal) / self.nominal * 1e6

    def drift_alarm(self) -> bool:
        """漂移是否超告警阈值（>200ppm）。"""
        return abs(self.drift_ppm()) > DRIFT_WARN_PPM

    def reset(self) -> None:
        """清空统计（重新开始观察窗口）。"""
        self._intervals.clear()
        self._last_ts = None
        self._late_events = 0
