"""2oo3 速度表决（Two-out-of-Three voting）—— 对标真实列控的速度传感器冗余。

真实列控（ATP/TCMS）通常配置多个速度传感器（雷达/测速电机/多普勒），
对同一物理量给出多个测量值。2oo3 表决：
    - 三个通道中至少两个一致（容差内）才采信该速度 —— 容忍单通道故障
    - 输出"表决速度"与"通道健康状态"
    - 低于 2 个通道一致 → 速度无效（触发降级/紧急制动决策）

设计原则（与 EBM 的 SIL2 双通道一致）：
    表决冗余度与安全等级挂钩——2oo3 能容忍单通道漂移/失效，且不会因
    单一通道噪声而误判（防误报），符合 SIL2 级"多数一致才动作"思想。
    单通道故障时自动降级为 2oo2（architecture='2oo2'）继续服务——
    容错演进（fault-tolerant degradation）：降级后一致性要求更严格，
    两通道必须容差内一致才采信，防单通道残余误差污染表决结果。
"""

# 通道索引（物理传感器通道）
CH_A, CH_B, CH_C = 0, 1, 2
VALID_CHANNELS = (CH_A, CH_B, CH_C)

# 容差：两通道读数差小于等于该值视为"一致"（km/h）
VOTE_TOLERANCE_KMH = 2.0

# 表决结果状态
VOTE_VALID = "valid"  # ≥2 通道一致，输出多数速度
VOTE_DIVERGENT = "divergent"  # 无 2 通道一致，速度无效
VOTE_FAILED = "failed"  # 通道故障数 ≥2，表决器失效


class SpeedVoter2oo3:
    """2oo3 速度表决器：三通道输入 → 表决速度 + 健康状态。

    用法：
        voter = SpeedVoter2oo3()
        ok, speed, state = voter.vote([80.0, 81.0, 200.0])
    """

    def __init__(self, tolerance_kmh: float = VOTE_TOLERANCE_KMH):
        self.tolerance_kmh = tolerance_kmh
        self._faulty: set[int] = set()  # 故障通道集合
        self._degrade_events = 0  # 降级事件计数（2oo3→2oo2 或恢复）

    @property
    def faulty_channels(self) -> set[int]:
        """当前故障通道集合（副本）。"""
        return set(self._faulty)

    @property
    def healthy_channels(self) -> list[int]:
        """当前健康通道索引（按通道序）。"""
        return [c for c in VALID_CHANNELS if c not in self._faulty]

    @property
    def architecture(self) -> str:
        """当前表决架构：'2oo3'（3 通道）/ '2oo2'（降级，2 通道）/ 'failed'（<2 通道）。

        对标真实冗余架构：单通道故障时 2oo3 自动降级为 2oo2 继续服务
        （容错演进），只有健康通道不足 2 个才判定表决器失效。
        """
        n = len(self.healthy_channels)
        if n >= 3:
            return "2oo3"
        if n == 2:
            return "2oo2"
        return "failed"

    @property
    def degraded(self) -> bool:
        """是否处于降级架构（2oo2，即单通道故障）。"""
        return self.architecture == "2oo2"

    @property
    def degrade_events(self) -> int:
        """降级/恢复事件累计计数（每次架构在 2oo3↔2oo2 间切换 +1）。"""
        return self._degrade_events

    def mark_faulty(self, channel: int) -> None:
        """标记某通道故障（对应传感器失效/断线）。

        首次故障触发 2oo3→2oo2 降级（计一次降级事件）——
        真实列控在此时降级为 2oo2 表决并提示维护，而不是直接失效。
        """
        if channel not in VALID_CHANNELS:
            raise ValueError(f"未知通道: {channel}（合法 {VALID_CHANNELS}）")
        if channel in self._faulty:
            return
        self._faulty.add(channel)
        if len(self.healthy_channels) == 2:
            self._degrade_events += 1

    def clear_fault(self, channel: int) -> None:
        """清除某通道故障标记（传感器恢复）。恢复 3 通道时计一次恢复事件。"""
        if channel not in VALID_CHANNELS:
            raise ValueError(f"未知通道: {channel}（合法 {VALID_CHANNELS}）")
        if channel not in self._faulty:
            return
        self._faulty.discard(channel)
        if len(self.healthy_channels) == 3:
            self._degrade_events += 1

    def vote(self, speeds: list[float]) -> tuple[bool, float, str]:
        """三通道速度表决。

        参数:
            speeds: 三个通道的速度读数（km/h）。故障通道读数忽略。
        返回:
            (是否有效, 表决速度, 状态)
            状态: valid / divergent / failed
        """
        if len(speeds) != 3:
            raise ValueError(f"需要 3 个通道读数，收到 {len(speeds)}")

        healthy = [s for i, s in enumerate(speeds) if i not in self._faulty]
        if len(healthy) < 2:
            # 可用通道不足 2 个：表决器失效
            return False, 0.0, VOTE_FAILED

        if len(healthy) == 2:
            # 2oo2 降级表决：两通道必须一致（容差内）才采信——降级后
            # 失去多数仲裁能力，一致性要求更严格（防单通道故障误报）
            if abs(healthy[0] - healthy[1]) <= self.tolerance_kmh:
                return True, (healthy[0] + healthy[1]) / 2.0, VOTE_VALID
            return False, 0.0, VOTE_DIVERGENT

        # 2oo3 表决：找多数一致组（容差内两两匹配）
        for i in range(len(healthy)):
            for j in range(i + 1, len(healthy)):
                if abs(healthy[i] - healthy[j]) <= self.tolerance_kmh:
                    agreed = (healthy[i] + healthy[j]) / 2.0
                    return True, agreed, VOTE_VALID

        return False, 0.0, VOTE_DIVERGENT
