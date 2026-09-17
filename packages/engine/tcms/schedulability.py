"""CAN 可调度性分析（Schedulability）—— Tindell 最坏响应时间（WCRT）与 ID 分配审计。

对标真实网络设计流程：报文集定义后必须做可调度性分析，证明每个周期
报文的最坏响应时间 R 不超过其周期（deadline = period），否则该报文会
"赶不上下一拍"——真实整车厂用 Vector CANdb++/网络设计工具做同样计算。

Tindell 迭代公式（CAN 固定优先级非抢占调度）：
    R = B + C + Σ_{j∈hp(i)} ⌈(R + τ_bit + J_j) / T_j⌉ · C_j
其中：
    B    = 低优先级帧正在传输造成的阻塞（最坏 = max C_lp）
    C_i  = 报文 i 的传输时间（最坏填充位）
    τ_bit= 1 bit 时间（仲裁粒度）
    T_j  = 高优先级报文周期
    J_j  = 高优先级报文队列抖动（0 = 理想）
迭代到不动点；若 R > deadline(=周期) 则报文不可调度。

审计结论形态（面试亮点）：
    - 输出逐报文 WCRT 表 + "可调度/超限"判定
    - 汇总：总线利用率 U = Σ C_i/T_i（U 与负载率同源，负载率是 U 的实测版）
    - ID 分配审计：ID 越小优先级越高，紧急制动/安全报文必须占低 ID 段
      ——本项目 0x700 BrakeSystem 的优先级低于 0x200 VehicleSpeed 的事实，
      恰好演示"周期与优先级不匹配"的风险识别过程。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .busload import frame_bits


@dataclass(frozen=True)
class MessageSpec:
    """一条 CAN 报文的调度参数。"""

    arb_id: int  # 仲裁 ID（越小优先级越高）
    name: str
    dlc: int
    period_s: float  # 周期（deadline = 周期）
    jitter_s: float = 0.0  # 队列抖动
    segment: str = ""  # 网段（vehicle/comfort/backbone；''=未分组单总线）


@dataclass
class WcrtResult:
    """单报文 WCRT 分析结果。"""

    spec: MessageSpec
    wcrt_s: float
    deadline_s: float
    schedulable: bool
    iterations: int
    blocking_s: float
    interference_s: float
    interference_by: dict = field(default_factory=dict)


def transmission_time_s(dlc: int, bitrate: int) -> float:
    """传输时间 C（最坏填充位）。"""
    return frame_bits(dlc) / bitrate


def analyse_wcrt(
    spec: MessageSpec,
    higher_priority: list[MessageSpec],
    bitrate: int = 250_000,
    max_iterations: int = 1000,
) -> WcrtResult:
    """计算单报文 WCRT（Tindell 迭代）。

    higher_priority：所有仲裁 ID 更小（优先级更高）的报文。
    迭代 R 直到不动点或超过 max_iterations。
    """
    c = transmission_time_s(spec.dlc, bitrate)
    deadline = spec.period_s
    tau = 1.0 / bitrate  # 1 bit 时间
    # 阻塞 B：低优先级报文正在传输的最坏情况（取所有 C 的最大值，含自身）
    blocking = c  # 至少自身；调用方可提供完整报文集以取 max
    r_prev = c
    iterations = 0
    interference_by: dict[str, float] = {}
    while True:
        iterations += 1
        if iterations > max_iterations:
            break
        interference = 0.0
        by: dict[str, float] = {}
        for hp in higher_priority:
            if hp.period_s <= 0:
                continue
            term = math.ceil((r_prev + tau + hp.jitter_s) / hp.period_s) * transmission_time_s(
                hp.dlc, bitrate
            )
            interference += term
            by[hp.name] = term
        r_new = blocking + interference
        interference_by = by
        if r_new == r_prev:
            r_prev = r_new
            break
        if r_new > deadline:
            r_prev = r_new
            break
        r_prev = r_new
    return WcrtResult(
        spec=spec,
        wcrt_s=r_prev,
        deadline_s=deadline,
        schedulable=(r_prev <= deadline),
        iterations=iterations,
        blocking_s=blocking,
        interference_s=sum(interference_by.values()),
        interference_by=interference_by,
    )


class SchedulabilityAnalyser:
    """报文集可调度性分析器：利用率、逐报文 WCRT、ID 分配审计。"""

    def __init__(self, messages: list[MessageSpec], bitrate: int = 250_000):
        if bitrate <= 0:
            raise ValueError(f"bitrate 必须为正数，got {bitrate}")
        if not messages:
            raise ValueError("报文集不能为空")
        for m in messages:
            if m.period_s <= 0:
                raise ValueError(f"{m.name} 的周期必须为正数")
        self.bitrate = bitrate
        # 按优先级排序（ID 升序）
        self.messages = sorted(messages, key=lambda m: m.arb_id)

    @property
    def utilization(self) -> float:
        """总线利用率 U = Σ C_i / T_i（与负载率的理论同源量）。"""
        return sum(transmission_time_s(m.dlc, self.bitrate) / m.period_s for m in self.messages)

    @property
    def blocking_source(self) -> MessageSpec | None:
        """阻塞源 B：低优先级帧传输时间最大者（最坏阻塞）。"""
        if not self.messages:
            return None
        return max(self.messages, key=lambda m: transmission_time_s(m.dlc, self.bitrate))

    def analyse_all(self) -> list[WcrtResult]:
        """逐报文 WCRT 分析（按优先级升序）。"""
        results = []
        for i, spec in enumerate(self.messages):
            higher = self.messages[:i]  # 之前 = ID 更小 = 优先级更高
            r = analyse_wcrt(spec, higher, bitrate=self.bitrate)
            results.append(r)
        return results

    def analyse_all_with_blocking(self) -> list[WcrtResult]:
        """逐报文 WCRT 分析，阻塞项取全报文集最坏值（真实口径）。"""
        worst = self.blocking_source
        worst_c = transmission_time_s(worst.dlc, self.bitrate)
        results = []
        for i, spec in enumerate(self.messages):
            higher = self.messages[:i]
            r = analyse_wcrt(spec, higher, bitrate=self.bitrate)
            if r.blocking_s < worst_c:
                r.blocking_s = worst_c
                r.wcrt_s = worst_c + r.interference_s
                r.schedulable = r.wcrt_s <= r.deadline_s
            results.append(r)
        return results

    def report(self) -> dict:
        """可调度性报告：利用率 + 逐报文判定 + 总体结论。"""
        results = self.analyse_all_with_blocking()
        rows = []
        for r in results:
            rows.append(
                {
                    "arb_id": hex(r.spec.arb_id),
                    "name": r.spec.name,
                    "period_ms": round(r.spec.period_s * 1e3, 1),
                    "wcrt_ms": round(r.wcrt_s * 1e3, 3),
                    "deadline_ms": round(r.deadline_s * 1e3, 1),
                    "schedulable": r.schedulable,
                    "margin_pct": round(100.0 * (r.deadline_s - r.wcrt_s) / r.deadline_s, 1),
                }
            )
        return {
            "bitrate": self.bitrate,
            "utilization_pct": round(100.0 * self.utilization, 3),
            "all_schedulable": all(r.schedulable for r in results),
            "unschedulable": [r for r in rows if not r["schedulable"]],
            "rows": rows,
        }


def audit_id_assignment(messages: list[MessageSpec], safety_names: set[str]) -> dict:
    """ID 分配审计：安全关键报文是否占据高优先级（低 ID）段。

    规则：safety_names 中的报文 ID 应小于所有非安全报文的 ID
    （安全报文必须优先于普通报文仲裁获胜）。
    """
    safety = [m for m in messages if m.name in safety_names]
    ordinary = [m for m in messages if m.name not in safety_names]
    if not safety or not ordinary:
        return {"ok": True, "reason": "报文集不完整（缺安全/普通报文）", "violations": []}
    max_safety_id = max(m.arb_id for m in safety)
    violations = [
        {"name": m.name, "arb_id": m.arb_id} for m in ordinary if m.arb_id < max_safety_id
    ]
    return {
        "ok": not violations,
        "reason": (
            "安全报文全部占据高优先级段"
            if not violations
            else f"存在 {len(violations)} 条普通报文 ID 低于安全报文最差 ID"
        ),
        "violations": violations,
    }


# ---- 多网段可调度性（Q2-P-A ③-c）----


def group_messages_by_segment(messages: list[MessageSpec]) -> dict[str, list[MessageSpec]]:
    """按网段分组（保持组内按 ID 排序交给 analyser）。"""
    groups: dict[str, list[MessageSpec]] = {}
    for m in messages:
        groups.setdefault(m.segment, []).append(m)
    return groups


def analyse_dbc_by_segment(db, bitrate: int = 250_000) -> dict:
    """用真实 DBC + GenMsgSegment 做网段级可调度性分析。

    每个网段独立建 analyser（同网段帧才竞争仲裁 → WCRT 只在同段内计算），
    返回逐网段报告 + 总体结论。事件型帧（无周期）不参与。
    """
    from . import protocol as _proto

    segmap = _proto.load_segment_map()
    per_name: dict[str, MessageSpec] = {}
    for msg in db.messages:
        cycle_ms = msg.cycle_time or 0
        if cycle_ms <= 0:
            continue
        seg = segmap.get(msg.frame_id, "")
        per_name[msg.name] = MessageSpec(
            arb_id=msg.frame_id,
            name=msg.name,
            dlc=msg.length,
            period_s=cycle_ms / 1000.0,
            segment=seg,
        )
    groups = group_messages_by_segment(list(per_name.values()))
    segments: dict[str, dict] = {}
    all_schedulable = True
    worst = 0.0
    for seg_name in sorted(groups):
        an = SchedulabilityAnalyser(groups[seg_name], bitrate=bitrate)
        rep = an.report()
        rep["frames"] = len(groups[seg_name])
        rep["segments"] = seg_name
        segments[seg_name] = rep
        all_schedulable = all_schedulable and bool(rep["all_schedulable"])
        worst = max(worst, rep["utilization_pct"])
    return {
        "bitrate": bitrate,
        "segments": segments,
        "segment_count": len(segments),
        "all_schedulable": all_schedulable,
        "worst_segment_utilization_pct": round(worst, 3),
    }
