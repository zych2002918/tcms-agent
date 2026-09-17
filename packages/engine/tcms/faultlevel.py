"""故障等级模型（Fault Level Classification）—— 对标铁路功能安全分级思想。

真实列控系统（EN 50126 RAMS / IEC 61508）按故障后果严重度分级，
不同等级对应不同处置策略（报警/降级/紧急制动/停车）。本模块：
    - 定义四级故障模型：轻微 / 一般 / 严重 / 灾难
    - 提供"故障 → 等级 → 处置动作"的映射
    - 提供故障注入编排器（按等级编排注入序列 + 影响评估）

设计原则：故障分级与处置策略分离——等级由"后果严重度"决定，
处置由"当前运行模式"决定（同一等级故障在不同模式下处置不同）。
"""

# ---- 故障等级 ----
LEVEL_INFO = "info"  # 轻微：不影响运行，仅提示
LEVEL_MINOR = "minor"  # 一般：影响舒适性/效率，可继续运行
LEVEL_MAJOR = "major"  # 严重：影响安全，需降级/限速
LEVEL_CRITICAL = "critical"  # 灾难：危及安全，立即紧急制动/停车

VALID_LEVELS = (LEVEL_INFO, LEVEL_MINOR, LEVEL_MAJOR, LEVEL_CRITICAL)

# 等级排序（用于比较/编排）
LEVEL_ORDER = {LEVEL_INFO: 0, LEVEL_MINOR: 1, LEVEL_MAJOR: 2, LEVEL_CRITICAL: 3}

# ---- 处置动作 ----
ACTION_NONE = "none"  # 仅记录
ACTION_WARNING = "warning"  # 司机提示
ACTION_DERATE = "derate"  # 降级运行（限速/降功率）
ACTION_EB = "emergency_brake"  # 紧急制动
ACTION_SHUTDOWN = "shutdown"  # 停车/断电

# 等级 → 默认处置（模式无关的保守处置；可被模式覆盖）
LEVEL_ACTION = {
    LEVEL_INFO: ACTION_NONE,
    LEVEL_MINOR: ACTION_WARNING,
    LEVEL_MAJOR: ACTION_DERATE,
    LEVEL_CRITICAL: ACTION_EB,
}

# 处置优先级（用于合并多个故障时取最高）
ACTION_PRIORITY = {
    ACTION_NONE: 0,
    ACTION_WARNING: 1,
    ACTION_DERATE: 2,
    ACTION_EB: 3,
    ACTION_SHUTDOWN: 4,
}

# ---- 故障定义 ----
FAULTS: dict[str, dict] = {
    # 轻微（info）：记录提示
    "soc_low": {"level": LEVEL_INFO, "desc": "SOC 偏低，建议充电"},
    "temp_high": {"level": LEVEL_INFO, "desc": "温度偏高，监控中"},
    # 一般（minor）：告警
    "door_sensor_noise": {"level": LEVEL_MINOR, "desc": "车门传感器偶发噪声"},
    "speed_sensor_drift": {"level": LEVEL_MINOR, "desc": "速度传感器轻微漂移"},
    # 严重（major）：降级
    "door_fault": {"level": LEVEL_MAJOR, "desc": "车门故障，按未关处理"},
    "traction_loss": {"level": LEVEL_MAJOR, "desc": "牵引丢失，降级运行"},
    "overspeed": {"level": LEVEL_MAJOR, "desc": "超速，需限速"},
    # 灾难（critical）：紧急制动/停车
    "eb_failure": {"level": LEVEL_CRITICAL, "desc": "紧急制动执行失败"},
    "traction_brake_conflict": {"level": LEVEL_CRITICAL, "desc": "牵引制动冲突"},
    "pantograph_arc": {"level": LEVEL_CRITICAL, "desc": "受电弓拉弧风险"},
}


def classify(fault_name: str) -> dict:
    """查询故障等级定义。未知故障抛 ValueError。"""
    if fault_name not in FAULTS:
        raise ValueError(f"未知故障: {fault_name}（已知 {list(FAULTS)}）")
    return dict(FAULTS[fault_name])


def action_for(fault_name: str, mode: str = "auto") -> str:
    """故障 → 处置动作（按当前运行模式）。

    模式敏感处置：CRITICAL 故障在任何模式都紧急制动（安全不可妥协）；
    MAJOR 故障在 ATO/CM 下降级，在 RM 下仅告警（司机人工处置）。
    """
    info = classify(fault_name)
    level = info["level"]
    if level == LEVEL_CRITICAL:
        return ACTION_EB
    if level == LEVEL_MAJOR:
        return ACTION_DERATE if mode != "rm" else ACTION_WARNING
    if level == LEVEL_MINOR:
        return ACTION_WARNING
    return ACTION_NONE


def merge_actions(actions: list[str]) -> str:
    """合并多个处置动作：取最高优先级。"""
    best = ACTION_NONE
    for a in actions:
        if ACTION_PRIORITY[a] > ACTION_PRIORITY[best]:
            best = a
    return best


class FaultInjector:
    """故障注入编排器：按等级编排注入序列 + 影响评估。

    用法：
        fi = FaultInjector()
        fi.inject("overspeed")
        fi.inject("eb_failure")
        fi.report()   # {'actions': 'emergency_brake', 'faults': [...], 'worst_level': 'critical'}
    """

    def __init__(self):
        self._active: dict[str, dict] = {}  # fault_name -> 故障定义

    @property
    def active_faults(self) -> list[str]:
        return list(self._active)

    def inject(self, fault_name: str) -> str:
        """注入故障（可叠加）。返回该故障的处置动作。"""
        info = classify(fault_name)
        self._active[fault_name] = info
        return action_for(fault_name)

    def clear(self, fault_name: str) -> None:
        """清除某故障（恢复）。"""
        self._active.pop(fault_name, None)

    def clear_all(self) -> None:
        """清除全部故障。"""
        self._active.clear()

    def worst_level(self) -> str:
        """当前最严重故障等级（无故障返回 info）。"""
        if not self._active:
            return LEVEL_INFO
        return max((d["level"] for d in self._active.values()), key=lambda l: LEVEL_ORDER[l])

    def report(self) -> dict:
        """影响评估报告。"""
        actions = [action_for(f) for f in self._active]
        return {
            "faults": list(self._active),
            "worst_level": self.worst_level(),
            "actions": merge_actions(actions),
        }
