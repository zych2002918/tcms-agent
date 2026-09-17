"""超速监督与动态 EBI 曲线（ATP 速度监督）—— 对标真实列控 ATP 的 SBI/EBI 三级监督。

真实列车自动防护（ATP，如 ETCS 的 SRS 规范）对速度做**分级监督**：
    - 告警速度（Warning）：提示司机接近限速
    - SBI（Service Brake Intervention，常用制动干预）：超速触发常用制动
    - EBI（Emergency Brake Intervention，紧急制动干预）：超速触发紧急制动

动态 EBI 曲线：允许速度随**位置/距离**变化（接近限速区时允许速度下降），
本模块实现"目标点限速"模型——列车当前位置离目标越近，允许速度越低
（线性逼近目标速度），形成随距离变化的 EBI 曲线。

设计原则：监督等级递进——Warning < SBI < EBI，间距可配置；
EBI 是安全底线，任何模式下都不可被覆盖（对齐 EBM 的 CRITICAL 处置）。
"""

# 监督等级
SUPERVISION_NONE = "none"  # 未超速
SUPERVISION_WARNING = "warning"  # 告警（接近限速）
SUPERVISION_SBI = "sbi"  # 常用制动干预
SUPERVISION_EBI = "ebi"  # 紧急制动干预

# 默认参数（模拟真实 ATP 的典型设置）
WARNING_OFFSET_KMH = 5.0  # 告警阈值 = 限速 - 5
SBI_OFFSET_KMH = 2.0  # SBI 阈值 = 限速 - 2
EBI_OFFSET_KMH = 0.0  # EBI 阈值 = 限速（超速即紧急制动）
DEFAULT_LIMIT_KMH = 160.0  # 默认线路限速


class SpeedSupervisor:
    """速度监督器：速度 → 监督等级（Warning/SBI/EBI）。

    用法：
        sup = SpeedSupervisor(limit_kmh=160)
        sup.evaluate(165.0)   # 'warning'（>155）
        sup.evaluate(159.0)   # 'sbi'（>158）
        sup.evaluate(160.5)   # 'ebi'（>160）
    """

    def __init__(
        self,
        limit_kmh: float = DEFAULT_LIMIT_KMH,
        warning_offset: float = WARNING_OFFSET_KMH,
        sbi_offset: float = SBI_OFFSET_KMH,
        ebi_offset: float = EBI_OFFSET_KMH,
    ):
        self.limit = limit_kmh
        self.warning_offset = warning_offset
        self.sbi_offset = sbi_offset
        self.ebi_offset = ebi_offset

    def thresholds(self) -> dict:
        """当前阈值：warning/sbi/ebi（km/h）。"""
        return {
            "warning": self.limit - self.warning_offset,
            "sbi": self.limit - self.sbi_offset,
            "ebi": self.limit - self.ebi_offset,
        }

    def evaluate(self, speed_kmh: float, speed_valid: bool = True) -> str:
        """按当前限速评估速度 → 监督等级。速度无效返回 none（不误判）。"""
        if not speed_valid:
            return SUPERVISION_NONE
        th = self.thresholds()
        if speed_kmh > th["ebi"]:
            return SUPERVISION_EBI
        if speed_kmh > th["sbi"]:
            return SUPERVISION_SBI
        if speed_kmh > th["warning"]:
            return SUPERVISION_WARNING
        return SUPERVISION_NONE


class DynamicEbiCurve:
    """动态 EBI 曲线：目标点限速模型（允许速度随距离线性逼近目标速度）。

    模型：列车在距目标点距离 d 处的允许速度 =
        target_speed + (approach_slope) * d
    其中 approach_slope 由"当前允许速度"与"目标速度差"除以"制动距离"得出，
    保证列车在到达目标点时速度降到目标速度以下。

    用法：
        curve = DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=800)
        curve.allowed_at(distance_m=400)   # 该点的允许速度
        curve.is_overspeed(80.0, 400)      # 该点 80km/h 是否超 EBI
    """

    def __init__(
        self,
        target_speed_kmh: float,
        current_speed_kmh: float,
        brake_distance_m: float,
    ):
        if brake_distance_m <= 0:
            raise ValueError("制动距离必须 > 0")
        if current_speed_kmh < target_speed_kmh:
            raise ValueError("当前速度必须 ≥ 目标速度")
        self.target = target_speed_kmh
        self.current = current_speed_kmh
        self.brake_distance = brake_distance_m
        # 斜率：速度差 / 距离（线性逼近）
        self._slope = (current_speed_kmh - target_speed_kmh) / brake_distance_m

    def allowed_at(self, distance_m: float) -> float:
        """距目标点 distance_m 处的允许速度（线性逼近目标速度）。

        距离越大允许速度越高（接近当前速度），到目标点归为目标速度。
        """
        if distance_m < 0:
            raise ValueError("距离不能为负")
        allowed = self.target + self._slope * distance_m
        return min(allowed, self.current)  # 上限为当前允许速度

    def is_overspeed(self, speed_kmh: float, distance_m: float) -> bool:
        """该点速度是否超过允许速度（即超 EBI）。"""
        return speed_kmh > self.allowed_at(distance_m)

    def braking_point(self, speed_kmh: float) -> float:
        """以某速度运行时，距离目标点多远处需要开始制动（EBI 触发点）。

        即该速度恰好等于允许速度的距离：d = (speed - target) / slope。
        """
        if speed_kmh <= self.target:
            return 0.0
        return (speed_kmh - self.target) / self._slope
