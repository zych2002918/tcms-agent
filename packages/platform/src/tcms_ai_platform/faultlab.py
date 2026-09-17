"""FaultLab —— 故障场景「演示动画」的数据重建器。

把一次真实故障场景(声明式 YAML)重建为一条可播放的事件时间线 + 通道曲线，
供前端以列车/驾驶台动画演示「故障如何发生、如何被检测、系统如何处置」。

诚实性纪律(与全仓库一致，数字机器自证) —— 以「数据管线透明」表达，而非道歉：
- 每条事件带结构化 source{kind,ref,desc}，真实度分级：
    scenario_yaml  注入/恢复时刻与期望处置 = 真实场景 YAML 步骤(assets 派生)
    fault_dict     检测/恢复语义文本与默认处置 = 真实故障字典(faults.yaml)
    engine_assert  处置 actual = 真实引擎 run_result 断言(若已执行)
    derived_phys   检测时刻 = 注入 + DETECT_DELAY_S(示意规则)；通道波形 = 示意物理模型
    note           备注(如"场景未编排恢复步骤"的事实陈述)
- 通道波形(车速/制动缸压/门状态/心跳/总线…)为事件级示意重建：只按
  「故障激活区间 × 真实阈值常量」做阶梯变化，不做逐周期总线级仿真。
  示意模型规则与真实/示意常量表见 demo["pipeline"]["constants"]，用户可逐条核对。
- 兼容字段：事件 derived 布尔保留，含义 = (source.kind == "derived_phys")。
- 引擎真实执行的黑盒开窗：demo["engine"]{asserted,version,trace,assertions,notes}。

通道在故障激活期间的取值来自各故障档案(profile)；档案值若非源码常量
则以 `derived: true` 标注，保持诚实。

用法：
    from tcms_ai_platform.faultlab import build_demo, build_demo_from_steps
    demo = build_demo(asset_model, "overspeed_derate.yaml", run_result=None)
    demo = build_demo_from_steps(asset_model, "my_seq", steps, run_result=None)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .core.models import AssetModel

# 采样步长（秒）——前端动画播放粒度
SAMPLE_S = 0.2
# 场景结束后多留几秒尾段，让演示有「处置后仍在运行」的观察窗口
TAIL_S = 4.0
# 检测到动作生效的示意延迟（秒；检测→处置的工程响应时间，非源码常量 → derived）
DETECT_DELAY_S = 0.4

# 常规「巡航」基线：出库加速到 120 km/h 后匀速（示意驾驶曲线，非源码常量）
CRUISE_KMH = 120.0


@dataclass
class DemoEvent:
    """时间线上一个事件(标记点)。

    source: 数据来源标注（引擎观察窗）。kind 分级见模块 docstring；
    ref 为来源引用（场景 YAML@版本 / faults.yaml 条目 / 引擎断言字段），
    desc 为一句话中文说明该数据怎么来的。derived 布尔兼容保留，
    含义 = source.kind == "derived_phys"。
    """

    t: float
    kind: str  # inject / detect / action / recover / note
    fault: str
    label: str
    detail: str = ""
    level: str = ""
    action: str = ""
    derived: bool = False  # True = 示意重建(检测延迟等)，False = 场景/字典真实
    source_kind: str = ""
    source_ref: str = ""
    source_desc: str = ""
    # 高亮提示增强（哪里异常 + 什么异常）：真实故障字典派生，事件到哪都能自解释
    fault_name: str = ""
    subsystem: str = ""  # 位置：故障字典子系统（照明/辅助电源/网络…）
    domain_zh: str = ""  # 位置：13 系统域中文标签（照明/辅助供电/网络列车控制…）

    def to_dict(self) -> dict:
        out = {
            "t": round(self.t, 2),
            "kind": self.kind,
            "fault": self.fault,
            "label": self.label,
            "detail": self.detail,
            "level": self.level,
            "action": self.action,
            "derived": self.derived,
        }
        if self.source_kind:
            out["source"] = {
                "kind": self.source_kind,
                "ref": self.source_ref,
                "desc": self.source_desc,
            }
        if self.fault_name:
            out["fault_name"] = self.fault_name
        if self.subsystem:
            out["subsystem"] = self.subsystem
        if self.domain_zh:
            out["domain_zh"] = self.domain_zh
        return out


def _fault_context(m: AssetModel, key: str) -> dict:
    """事件“哪里异常/什么异常”上下文（真实故障字典派生，不编造）。"""
    if key not in m.faults_by_key:
        return {}
    fd = m.fault(key)
    ctx: dict = {
        "fault_name": fd.name,
        "subsystem": fd.subsystem,
    }
    try:
        from .knowledge.vector import DOMAIN_ZH, subsystem_domain

        dom = subsystem_domain(fd.subsystem)
        if dom:
            ctx["domain_zh"] = DOMAIN_ZH.get(dom, dom)
    except Exception:  # noqa: BLE001 - 域标签缺失不阻断演示
        pass
    return ctx


def _fault_name(m: AssetModel, key: str) -> str:
    try:
        return m.fault(key).name
    except KeyError:
        return key


def _domain_alarm(fd) -> str:
    """⑤ 域特征演示档：未手工建档故障的结构化告警文案（语义真实、曲线明示示意）。

    格式：<故障名> 激活 · <子系统域>/<注入层> · 期望处置 <动作中文>
    （域特征示意 —— 数值曲线保持巡航基线，故障字典字段才是真实语义来源）
    """
    azh = _action_zh(fd.action) if fd.action else fd.action
    return (
        f"{fd.name} 激活 · {fd.subsystem}域/{fd.layer} · "
        f"期望处置 {azh}（域特征示意，曲线为巡航基线；检测/处置语义见故障字典）"
    )


# ---------------------------------------------------------------------------
# 故障档案：故障激活期间各通道的示意取值 + 检测语义（真实阈值锚定）
# ---------------------------------------------------------------------------
# 说明：值若来自上游源码常量/故障字典则写来源；示意演示值标注 derived。
# 通道集合：speed_kmh / brake_kpa / eb / doors_open / door_fault_count /
#          heartbeat_ok / bus_ok / soc / pantograph_ok / alarm_text
def _profiles() -> dict[str, dict]:
    """每故障的通道影响档案（只用真实语义；fallback 由 build_demo 兜底）。"""
    return {
        # ---- 牵引 / 速度 ----
        "overspeed": {
            "zh": "超速",
            "level": "major",
            "detect_zh": "ATP 速度监督判定超限",
            "note": "真实阈值：限速 160 km/h，Warning>155 / SBI>158 / EBI>160（atp.py 常量）",
            "speed": 168.0,  # 越过 EBI 160（derived 演示档）
            "alarm": "超速告警 Overspeed",
        },
        "traction_loss": {
            "zh": "牵引丢失",
            "level": "major",
            "detect_zh": "牵引可用性异常（手柄请求与速度矛盾）",
            "note": "处置 derate：限功率继续运行（示意演示档）",
            "alarm": "牵引丢失 TractionLoss",
            "speed": 90.0,
        },
        "speed_sensor_drift": {
            "zh": "速度传感器轻微漂移",
            "level": "minor",
            "detect_zh": "2oo3 表决不一致 / 变化率监测",
            "note": "轻微漂移 → warning 提示（示意演示档）",
            "alarm": "速度传感器漂移 SpeedDrift",
        },
        "sensor_stuck": {
            "zh": "传感器卡死",
            "level": "minor",
            "detect_zh": "信号变化率/新鲜度检查",
            "note": "warning 提示（示意演示档）",
            "alarm": "传感器无变化 SensorStuck",
        },
        # ---- 车门 ----
        "door_fault": {
            "zh": "车门故障(按未关处理)",
            "level": "major",
            "detect_zh": "DoorControl 状态枚举异常(Fault/Unknown)",
            "note": "真实枚举：Door1State 2=Fault / 3=Unknown（DBC VAL_）",
            "doors_open": 0,
            "door_fault_count": 1,
            "alarm": "车门故障 DoorFault",
        },
        "door_sensor_noise": {
            "zh": "车门传感器偶发噪声",
            "level": "minor",
            "detect_zh": "状态稳定性/时序检查(seqcheck)",
            "note": "瞬时跳变 → warning 提示（示意演示档）",
            "alarm": "门传感器噪声 DoorNoise",
        },
        # ---- 制动 / 冲突 ----
        "eb_failure": {
            "zh": "紧急制动执行失败",
            "level": "critical",
            "detect_zh": "执行反馈闭环(压力+回执+牵引切除三重证据)",
            "note": "真实机制：exec_feedback 三重证据；EB 命令发出但回路无响应",
            "alarm": "紧急制动无响应 EB_Fail",
            "eb_request": True,
            "eb_ok": False,  # 制动缸压力不上升（执行失败）
        },
        "traction_brake_conflict": {
            "zh": "牵引制动冲突",
            "level": "critical",
            "detect_zh": "联锁(interlocks.traction_brake_conflict)",
            "note": "真实联锁：手柄牵引位同时存在制动请求 → 冲突，须紧急制动",
            "alarm": "牵引制动冲突 Conflict",
            "eb": True,
        },
        "brake_actuator_stuck": {
            "zh": "制动执行器卡滞",
            "level": "major",
            "detect_zh": "EBR 硬线回路断点诊断 + 执行反馈超时",
            "note": "处置 derate：降级 + 冗余通道投入（示意演示档）",
            "alarm": "制动执行器故障 BrakeActuator",
        },
        # ---- 受电弓 / 能源 ----
        "pantograph_arc": {
            "zh": "受电弓拉弧风险",
            "level": "critical",
            "detect_zh": "联锁(弓状态×网压异常)",
            "note": "真实联锁：弓升起但接触网电压过低/过高 → 拉弧风险，降弓停车",
            "alarm": "受电弓拉弧 PantographArc",
            "eb": True,
            "pantograph_ok": False,
        },
        "soc_low": {
            "zh": "SOC 偏低",
            "level": "info",
            "detect_zh": "EnergyStatus.SocPercent 阈值监测",
            "note": "info 提示，仅建议充电（示意演示档）",
            "alarm": "电量低 SOC_Low",
            "soc": 25.0,
        },
        "temp_high": {
            "zh": "电池温度偏高",
            "level": "info",
            "detect_zh": "EnergyStatus.BatteryTemp 阈值监测",
            "note": "info 提示，温度回落自动恢复（示意演示档）",
            "alarm": "电池温度高 TempHigh",
        },
        # ---- 网络 / 心跳 / 总线 ----
        "heartbeat_loss_vcu": {
            "zh": "VCU 心跳丢失",
            "level": "major",
            "detect_zh": "看门狗健康表(超时/丢帧判定)",
            "note": "真实阈值：心跳周期 100ms、丢失 3 周期判离线、迟滞 2 周期恢复(watchdogs.py)",
            "alarm": "VCU 心跳丢失 HBeat_Loss",
            "heartbeat_ok": False,
        },
        "node_restart_storm": {
            "zh": "节点重启风暴",
            "level": "major",
            "detect_zh": "健康表反复 fault/active 翻转计数",
            "note": "真实机制：反复复位 → 心跳周期性中断（示意演示档）",
            "alarm": "节点重启风暴 RestartStorm",
            "heartbeat_ok": False,
        },
        "crc_error_frame": {
            "zh": "报文 CRC 校验错误",
            "level": "major",
            "detect_zh": "应用层 CRC-8 校验失败",
            "note": "真实机制：CRC-8 失败帧丢弃并计数(faults.py)",
            "alarm": "CRC 错误 CRC_Err",
            "bus_ok": False,
        },
        "bit_flip_frame": {
            "zh": "单比特翻转",
            "level": "major",
            "detect_zh": "CRC-8 校验失败或值域检查",
            "note": "真实机制：错误帧丢弃，后续正确帧恢复(faults.py)",
            "alarm": "位翻转 BitFlip",
            "bus_ok": False,
        },
        "rolling_counter_gap": {
            "zh": "滚动计数器跳变",
            "level": "minor",
            "detect_zh": "计数器连续性检查(seqcheck)",
            "note": "真实机制：alive/rolling counter 跳变 → 丢帧检测（示意演示档）",
            "alarm": "计数跳变 CounterGap",
        },
        "bus_short": {
            "zh": "总线短路",
            "level": "critical",
            "detect_zh": "错误状态机 Bus-Off(errstate)",
            "note": "真实阈值：TEC≥256 进 Bus-Off（errstate.py）",
            "alarm": "总线短路 Bus_Short",
            "bus_ok": False,
        },
        "bus_open_circuit": {
            "zh": "总线断路",
            "level": "major",
            "detect_zh": "错误状态机 Error-Passive + ACK 失败计数",
            "note": "真实阈值：TEC/REC ≥128 进 Error-Passive（errstate.py）",
            "alarm": "总线断路 Bus_Open",
            "bus_ok": False,
        },
        "bus_noise_burst": {
            "zh": "总线噪声突发",
            "level": "minor",
            "detect_zh": "REC/TEC 统计 + CRC 失败计数",
            "note": "真实机制：随机位错误突发 → 错误计数器上升（示意演示档）",
            "alarm": "总线噪声 Bus_Noise",
        },
        "arbitration_error": {
            "zh": "仲裁错误",
            "level": "minor",
            "detect_zh": "错误状态机仲裁错误计数",
            "note": "真实机制：发送重试（示意演示档）",
            "alarm": "仲裁错误 Arb_Err",
        },
        "short_frame": {
            "zh": "短帧(DLC 不足)",
            "level": "minor",
            "detect_zh": "DLC 校验(canlog/parser 长度检查)",
            "note": "真实机制：DLC 不足 → 信号不完整（示意演示档）",
            "alarm": "短帧 ShortFrame",
        },
        # ---- Wave A/B/C 域特征示例档案（通道级语义按故障字典保守映射）----
        "door_open_moving": {
            "zh": "运行中车门打开",
            "level": "critical",
            "detect_zh": "门-车联锁（移动×门开即 EB）",
            "note": "真实机制：移动中门开 = 联锁违规 → 立即紧急制动（interlocks）",
            "alarm": "运行中门开 DoorOpenMoving",
            "eb_request": True,
            "eb_applied": True,  # EB 施加：门开安全回路直接驱动（故障安全）
            "doors_open": 1,
        },
        "rear_door_fault": {
            "zh": "后车门故障(按未关处理)",
            "level": "major",
            "detect_zh": "DoorControlRear 状态枚举异常(2/3)",
            "note": "真实枚举：Door5-8 状态 2=Fault / 3=Unknown（DBC VAL_）",
            "door_fault_count": 1,
            "alarm": "后车门故障 RearDoorFault",
        },
        "battery_insulation_fault": {
            "zh": "电池绝缘监测报警",
            "level": "critical",
            "detect_zh": "绝缘监测单元报警（漏电流/阻抗阈值）",
            "note": "处置 shutdown：绝缘风险 → 安全分断高压（故障安全）",
            "alarm": "绝缘报警 InsulationFault",
        },
        "fire_multizone_alarm": {
            "zh": "多区烟火报警",
            "level": "critical",
            "detect_zh": "Fire_Detection.FireZone 多区触发判别",
            "note": "处置 shutdown：多区同时报警 → 立即停车疏散（烟火 SIL4）",
            "alarm": "多区烟火 MultiZoneFire",
        },
        "bogie_axle_overheat": {
            "zh": "轴温过高",
            "level": "major",
            "detect_zh": "Bogie_Monitor.Axle1-4Temp 阈值监测",
            "note": "处置 derate：轴温超限 → 限速运行至最近站（走行部）",
            "alarm": "轴温过高 AxleOverheat",
        },
        "hvac_cabin_overheat": {
            "zh": "客室温度过高",
            "level": "major",
            "detect_zh": "客室温度阈值监测（>28°C 持续）",
            "note": "处置 derate：夏季空调失效 → 舒适度降级（示意演示档）",
            "alarm": "客室过温 CabinOverheat",
        },
        "aux_converter_fault": {
            "zh": "辅助变流器故障",
            "level": "major",
            "detect_zh": "Aux_Converter.AuxConverterFault 状态位",
            "note": "处置 derate：低压负载降级（示意演示档）",
            "alarm": "辅助变流故障 AuxFault",
        },
        "gateway_segment_fault": {
            "zh": "网关网段故障",
            "level": "major",
            "detect_zh": "Gateway_Status.MvbSegmentA/B + GatewayFault",
            "note": "处置 derate：网段丢失 → 转发降级（示意演示档）",
            "alarm": "网段故障 GatewaySeg",
        },
    }


# ---------------------------------------------------------------------------
# 时间线重建
# ---------------------------------------------------------------------------


def _action_zh(action: str) -> str:
    return {
        "none": "仅记录（none）",
        "warning": "司机告警（warning）",
        "derate": "降级运行（derate）",
        "emergency_brake": "紧急制动（emergency_brake）",
        "shutdown": "停车/断电（shutdown）",
    }.get(action, action)


def build_demo(
    m: AssetModel,
    scenario_file: str,
    run_result: dict | None = None,
) -> dict:
    """把一个真实场景重建为演示时间线。

    - scenario_file 必须在 asset_model 场景中（否则抛 KeyError）。
    - run_result 可选：真实引擎 run 报告（tcms.scenarios.run_yaml 的返回值，
      含 assertions[{fault,ts,expected,actual,passed}] 与 ledger 台账汇总），
      用于替换「处置结果」来源为真实断言；缺省用故障字典 action。
    - 输出为「引擎观察窗」增强：
        events[].source{kind,ref,desc} —— 每条事件的数据来源标注；
        demo["engine"] —— 引擎真实执行的黑盒开窗（断言/台账证据）；
        demo["pipeline"] —— 数据管线静态描述 + 真实/示意常量表（透明自证）。
      既有字段(events/curve 结构、derived、params、duration、honesty)不变，
      前端向后兼容。

    与 build_demo_from_steps 共享同一套时间线重建核心
    （_demo_from_step_sources）：平台测试引用本签名，保持向后兼容。
    """
    scen = m.scenario(scenario_file)
    demo = _demo_from_step_sources(
        m,
        name=scen.name,
        file=scenario_file,
        ref_label=f"scenarios/{scenario_file}",
        steps=[_step_to_dict(st) for st in scen.steps],
        run_result=run_result,
    )
    return demo


def build_demo_from_steps(
    m: AssetModel,
    name: str,
    steps: list[dict],
    run_result: dict | None = None,
) -> dict:
    """从任意故障序列（与 ScenarioStep 同构的 dict 列表）重建演示时间线。

    - steps: [{at, action, fault, node, level, expect, impact}]，逐条与场景
      YAML 步骤同构；fault 必须是资产故障字典真实键（build_demo 由场景 YAML
      保证，本变体由调用方/端点预校验；越界键在重建时按 KeyError 诚实上抛，
      不做静默猜写）。
    - run_result: 可选真实引擎 run 报告（语义同 build_demo）。
    - 与 build_demo 输出同构（events/curve 输入面、engine、pipeline、honesty、
      params、duration）；区别仅在命名与数据来源标注：
        demo["scenario"]  = f"custom/{name}"（非 asset 场景文件，诚实标注）
        demo["scenario_name"] = name
        source.scenario_yaml 的 ref 对自定义序列显示 "custom/<name>"，
        desc 注明「来源为前端/编排自定义步骤序列（非资产场景 YAML）」。
    - 复用 build_demo 的事件重建/曲线/引擎窗口/管线逻辑（经共享核心）。
    """
    return _demo_from_step_sources(
        m,
        name=name,
        file=f"custom/{name}",
        ref_label=f"custom/{name}",
        steps=steps,
        run_result=run_result,
        custom=True,
    )


def _step_to_dict(st) -> dict:
    """ScenarioStep(或同构对象) → dict（供共享核心统一消费）。"""
    return {
        "at": float(st.at),
        "action": st.action,
        "fault": st.fault,
        "node": st.node,
        "level": st.level,
        "expect": st.expect,
        "impact": st.impact,
    }


def _demo_from_step_sources(
    m: AssetModel,
    name: str,
    file: str,
    ref_label: str,
    steps: list[dict],
    run_result: dict | None,
    custom: bool = False,
) -> dict:
    """共享核心：把「步骤序列」重建为演示时间线（build_demo 两变体共用）。

    - steps 为 dict 列表 [{at,action,fault,node,level,expect,impact}]；
    - custom=True 时数据来源标注使用 "custom/<name>" 前缀（诚实标注：
      这些步骤是编排的自定义序列，不是资产场景 YAML）；否则用资产场景文件。
    """
    prof = _profiles()
    events: list[DemoEvent] = []
    active: dict[str, float] = {}  # fault -> inject ts（用于派生检测事件去重）
    asserted_action: dict[str, str] = {}
    assert_index: dict[str, int] = {}

    if run_result:
        for i, a in enumerate(run_result.get("assertions", [])):
            if a.get("fault"):
                asserted_action.setdefault(a["fault"], a.get("actual", ""))
                assert_index.setdefault(a["fault"], i)

    if custom:
        steps = [dict(s) for s in steps]  # 防御：不修改调用方列表
    else:
        steps = list(steps)
    steps.sort(key=lambda s: float(s["at"]))
    last_t = float(steps[-1]["at"]) if steps else 0.0

    for st in steps:
        if st.get("action") == "inject" and st.get("fault"):
            fk = st["fault"]
            p = prof.get(fk, {})
            name_zh = _fault_name(m, fk)
            fd = m.fault(fk) if fk in m.faults_by_key else None
            ctx = _fault_context(m, fk)
            scen_ref = f"{ref_label}（step @{float(st['at']):.1f}s）"
            src_desc_inject = (
                "注入时刻/故障来自前端编排的自定义步骤序列（非资产场景 YAML，自定义编排）"
                if custom
                else "注入时刻/故障/期望处置来自真实场景 YAML 步骤（资产派生，真实）"
            )
            # 1) 注入（真实：场景 YAML 步骤 / 自定义：编排序列）
            events.append(
                DemoEvent(
                    t=float(st["at"]),
                    kind="inject",
                    fault=fk,
                    label=f"注入故障：{name_zh}",
                    detail=st.get("impact") or (fd.desc if fd else ""),
                    level=st.get("level") or (fd.level if fd else ""),
                    action=st.get("expect") or (fd.action if fd else ""),
                    source_kind="scenario_yaml",
                    source_ref=scen_ref,
                    source_desc=src_desc_inject,
                    fault_name=ctx.get("fault_name", ""),
                    subsystem=ctx.get("subsystem", ""),
                    domain_zh=ctx.get("domain_zh", ""),
                )
            )
            # 2) 检测（文本真实：故障字典 detect；时刻示意：注入 + DETECT_DELAY_S）
            detect_zh = p.get("detect_zh") or (fd.detect if fd else "系统检测到异常")
            events.append(
                DemoEvent(
                    t=round(float(st["at"]) + DETECT_DELAY_S, 2),
                    kind="detect",
                    fault=fk,
                    label="检测到异常",
                    detail=detect_zh,
                    level=st.get("level") or "",
                    derived=True,
                    source_kind="derived_phys",
                    source_ref=f"示意规则 DETECT_DELAY_S={DETECT_DELAY_S}s",
                    source_desc=(
                        "检测文本来自真实故障字典 faults.yaml(detect)；"
                        f"检测时刻 = 注入时刻 + 示意检测延迟 {DETECT_DELAY_S}s（示意物理模型规则）"
                    ),
                    fault_name=ctx.get("fault_name", ""),
                    subsystem=ctx.get("subsystem", ""),
                    domain_zh=ctx.get("domain_zh", ""),
                )
            )
            # 3) 处置（来源优先真实引擎断言 actual，否则故障字典 action）
            actual = asserted_action.get(fk) or (fd.action if fd else "")
            if fk in asserted_action:
                src_kind, src_ref, src_desc = (
                    "engine_assert",
                    f"run_result.assertions[{assert_index[fk]}].actual={actual!r}",
                    "处置 actual 来自真实引擎执行断言（tcms.scenarios.run_yaml，真实）",
                )
            else:
                src_kind, src_ref, src_desc = (
                    "fault_dict",
                    f"faults.yaml#{fk}.action",
                    "处置 actual 来自真实故障字典默认 action（未接引擎执行时回退，真实字典数据）",
                )
            events.append(
                DemoEvent(
                    t=round(float(st["at"]) + DETECT_DELAY_S, 2),
                    kind="action",
                    fault=fk,
                    label=f"处置：{_action_zh(actual)}",
                    detail=f"来源：{src_ref} · 期望 {st.get('expect') or fd.action}",
                    level=st.get("level") or "",
                    action=actual,
                    derived=False,
                    source_kind=src_kind,
                    source_ref=src_ref,
                    source_desc=src_desc,
                    fault_name=ctx.get("fault_name", ""),
                    subsystem=ctx.get("subsystem", ""),
                    domain_zh=ctx.get("domain_zh", ""),
                )
            )
            if p:
                active[fk] = float(st["at"])
        elif st.get("action") == "recover" and st.get("fault"):
            fk = st["fault"]
            fd = m.fault(fk) if fk in m.faults_by_key else None
            ctx = _fault_context(m, fk)
            events.append(
                DemoEvent(
                    t=float(st["at"]),
                    kind="recover",
                    fault=fk,
                    label=f"恢复：{_fault_name(m, fk)}",
                    detail=(fd.recovery if fd else "故障消除"),
                    level=fd.level if fd else "",
                    source_kind="scenario_yaml",
                    source_ref=f"{ref_label}（step @{float(st['at']):.1f}s）",
                    source_desc=(
                        "恢复时刻来自前端编排的自定义步骤序列（非资产场景 YAML，自定义编排）"
                        if custom
                        else "恢复时刻来自真实场景 YAML 步骤（资产派生，真实）"
                    ),
                    fault_name=ctx.get("fault_name", ""),
                    subsystem=ctx.get("subsystem", ""),
                    domain_zh=ctx.get("domain_zh", ""),
                )
            )
            active.pop(fk, None)

    # 若场景未显式恢复的故障仍在激活 → 尾注（事实陈述：不虚构恢复）
    if active:
        at_tail = last_t + 1.0
        events.append(
            DemoEvent(
                t=round(at_tail, 2),
                kind="note",
                fault=",".join(sorted(active)),
                label="场景结束，故障仍处处置状态",
                detail="本场景未编排恢复步骤；真实恢复语义见故障字典 recovery 字段。",
                source_kind="note",
                source_ref=f"{ref_label}（无 recover 步骤）",
                source_desc="备注：场景步骤未含恢复，演示不虚构恢复时刻（事实陈述）",
            )
        )

    duration = round(last_t + TAIL_S, 2)
    return {
        "scenario": file,
        "scenario_name": name,
        "faults": sorted({e.fault for e in events if e.kind in ("inject",)}),
        "steps": len(steps),
        "duration": duration,
        "sample_s": SAMPLE_S,
        "events": [e.to_dict() for e in sorted(events, key=lambda e: e.t)],
        # 档位参数：前端动画据此显示（derate 限速等，避免前端硬编码漂移）
        "params": {
            "limit_kmh": 160.0,  # 真实线路限速（atp.DEFAULT_LIMIT_KMH）
            "derate_speed": _DERATE_SPEED,  # 降级限速目标（示意演示档）
            "cruise_kmh": CRUISE_KMH,  # 正常巡航（示意）
            "eb_kpa": _EB_KPA,  # EB 制动缸压力基准
        },
        # 引擎真实执行的黑盒开窗（证据透明，不臆造）
        "engine": _engine_block(run_result),
        # 数据管线静态描述 + 真实/示意常量表
        "pipeline": _pipeline_block(),
        # 诚实性（保留字段名，前端/测试引用；内容改为积极客观的管线透明表述）
        "honesty": (
            "数据管线透明：每条事件标注 source——注入/恢复时刻与期望处置 = "
            + ("自定义编排步骤序列" if custom else "真实场景 YAML")
            + "；处置 actual = 真实引擎断言（若已执行）或故障字典 action；"
            "检测文本 = 真实故障字典；"
            "检测时刻与通道波形由示意物理模型生成（规则与真实/示意常量见 pipeline.constants）。"
        ),
    }


def _engine_block(run_result: dict | None) -> dict:
    """引擎真实执行的黑盒开窗：断言 + 台账证据（只在给了 run_result 时非空）。"""
    if not run_result:
        return {
            "asserted": False,
            "version": None,
            "trace": [],
            "assertions": [],
            "notes": [
                "本次演示未接入引擎执行（run_result=None）；处置 actual 来源见各事件 source=fault_dict。"
            ],
        }
    assertions = list(run_result.get("assertions") or [])
    # trace：引擎台账内部证据（ledger.report 的 open_faults 逐故障 stage 明细；
    # 场景若全部恢复则 open_faults 为空 → trace 空，但汇总计数进 notes，不虚构明细)
    trace: list[dict] = []
    ledger = run_result.get("ledger") or {}
    open_faults = ledger.get("open_faults") or []
    for fl in open_faults:
        trace.append(
            {
                "fault": fl.get("name"),
                "level": fl.get("level"),
                "source": fl.get("source"),
                "current_stage": fl.get("current_stage"),
                "stages": fl.get("stages") or [],
                "impact": fl.get("impact") or [],
            }
        )
    notes: list[str] = []
    version = run_result.get("engine_version") or run_result.get("version")
    if ledger:
        notes.append(
            f"引擎台账：total={ledger.get('total')}, open={ledger.get('open')}, "
            f"closed={ledger.get('closed')}, by_level={ledger.get('by_level')}"
        )
    if version is None:
        notes.append(
            "run_result 未携带引擎版本字段，version=None（不臆造；调用方可在 run_result 附 engine_version）"
        )
    if not assertions:
        notes.append("run_result 无 assertions（场景无 expect 断言或执行异常）")
    return {
        "asserted": True,
        "version": version,
        "trace": trace,
        "assertions": assertions,
        "notes": notes,
    }


def _pipeline_block() -> dict:
    """数据管线静态描述：FaultLab 的数据从哪来（真实/示意逐级标注 + 常量表）。"""
    return {
        "title": "演示数据管线",
        "steps": [
            {
                "name": "故障场景 YAML",
                "kind": "asset",
                "desc": "真实：scenarios/*.yaml 声明注入/恢复时刻、故障键与期望处置（资产快照派生）。",
            },
            {
                "name": "引擎真实执行",
                "kind": "engine",
                "desc": "真实：接入引擎时以 tcms.scenarios.run_yaml 断言 actual 作为处置结果；未接入时本级无产物，处置回退故障字典并如实标注 fault_dict。",
            },
            {
                "name": "FMEA 故障字典",
                "kind": "asset",
                "desc": "真实：faults.yaml 提供检测语义(detect)、恢复语义(recovery)与默认处置 action。",
            },
            {
                "name": "示意物理模型",
                "kind": "model",
                "desc": "示意：检测时刻 = 注入 + DETECT_DELAY_S；通道波形按真实阈值/枚举做事件级阶梯变化，非逐周期总线仿真。",
            },
        ],
        "constants": {
            "real": [
                {
                    "name": "limit_kmh",
                    "value": 160.0,
                    "unit": "km/h",
                    "source": "atp.py DEFAULT_LIMIT_KMH",
                    "desc": "线路限速（ATP 监督上限）",
                },
                {
                    "name": "speed_supervision",
                    "value": "Warning>155 / SBI>158 / EBI>160",
                    "unit": "km/h",
                    "source": "atp.py SpeedSupervisor",
                    "desc": "超速分级监督阈值",
                },
                {
                    "name": "eb_pressure_kpa",
                    "value": 300.0,
                    "unit": "kPa",
                    "source": "exec_feedback.py PRESSURE_APPLIED_KPA",
                    "desc": "判定制动已施加的制动缸压力",
                },
                {
                    "name": "heartbeat_period_ms",
                    "value": 100,
                    "unit": "ms",
                    "source": "watchdogs.py",
                    "desc": "心跳周期",
                },
                {
                    "name": "heartbeat_offline_misses",
                    "value": 3,
                    "unit": "周期",
                    "source": "watchdogs.py",
                    "desc": "丢失 3 周期判离线",
                },
                {
                    "name": "heartbeat_recover_misses",
                    "value": 2,
                    "unit": "周期",
                    "source": "watchdogs.py",
                    "desc": "迟滞 2 周期恢复",
                },
                {
                    "name": "bus_off_tec",
                    "value": 256,
                    "unit": "TEC",
                    "source": "errstate.py BUS_OFF_THRESHOLD",
                    "desc": "TEC≥256 进 Bus-Off",
                },
                {
                    "name": "error_passive_min",
                    "value": 128,
                    "unit": "TEC/REC",
                    "source": "errstate.py ERROR_PASSIVE_MIN",
                    "desc": "≥128 进 Error-Passive",
                },
                {
                    "name": "door_state_enum",
                    "value": "Door1State 2=Fault / 3=Unknown",
                    "unit": "-",
                    "source": "tcms.dbc VAL_",
                    "desc": "车门状态枚举",
                },
                {
                    "name": "crc8_drop",
                    "value": "CRC-8 失败帧丢弃并计数",
                    "unit": "-",
                    "source": "faults.py",
                    "desc": "应用层 CRC 校验机制",
                },
            ],
            "schematic": [
                {
                    "name": "detect_delay_s",
                    "value": DETECT_DELAY_S,
                    "unit": "s",
                    "source": "faultlab.py 示意规则",
                    "desc": "检测→处置的示意响应延迟（非源码常量）",
                },
                {
                    "name": "sample_s",
                    "value": SAMPLE_S,
                    "unit": "s",
                    "source": "faultlab.py",
                    "desc": "采样/播放步长",
                },
                {
                    "name": "tail_s",
                    "value": TAIL_S,
                    "unit": "s",
                    "source": "faultlab.py",
                    "desc": "处置后观察尾段时长",
                },
                {
                    "name": "cruise_kmh",
                    "value": _CRUISE_KMH,
                    "unit": "km/h",
                    "source": "faultlab.py 示意",
                    "desc": "正常巡航速度基线",
                },
                {
                    "name": "accel_rate",
                    "value": _ACCEL_RATE,
                    "unit": "km/h/s",
                    "source": "faultlab.py 示意",
                    "desc": "加速率",
                },
                {
                    "name": "derate_speed",
                    "value": _DERATE_SPEED,
                    "unit": "km/h",
                    "source": "faultlab.py 示意",
                    "desc": "降级限速目标",
                },
                {
                    "name": "eb_decel",
                    "value": _EB_DECEL,
                    "unit": "km/h/s",
                    "source": "faultlab.py 示意",
                    "desc": "EB/停车减速率",
                },
                {
                    "name": "derate_decel",
                    "value": _DERATE_DECEL,
                    "unit": "km/h/s",
                    "source": "faultlab.py 示意",
                    "desc": "降级减速率",
                },
                {
                    "name": "coast_decel",
                    "value": _COAST_DECEL,
                    "unit": "km/h/s",
                    "source": "faultlab.py 示意",
                    "desc": "牵引丢失滑行衰减",
                },
                {
                    "name": "eb_fail_coast",
                    "value": _EB_FAIL_COAST,
                    "unit": "km/h/s",
                    "source": "faultlab.py 示意",
                    "desc": "EB 执行失败时几乎不减速（命令≠执行教学点）",
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# 通道曲线：按时间推进计算各通道状态（供前端逐帧动画）
# ---------------------------------------------------------------------------
# 物理模型为「示意」（derived）：加速/巡航/制动减速仅用于演示处置效果，
# 数字依据真实阈值（160 限速 / EB 300kPa / 心跳 100ms 等），非逐周期仿真。

# 加速率 / 巡航（示意）
_ACCEL_RATE = 40.0  # km/h 每秒（3s 到 120 巡航）
_DERATE_SPEED = 90.0  # derate 限速目标（示意演示档，非源码常量）
_EB_DECEL = 50.0  # EB/停车 减速 km/h 每秒（示意）
_DERATE_DECEL = 25.0  # 降级减速（示意）
_COAST_DECEL = 2.5  # 牵引丢失/无动力滑行衰减（示意）
_EB_FAIL_COAST = 0.8  # EB 执行失败：几乎不减速（教学点：命令≠执行）
_EB_KPA = 300.0  # EB 制动缸压力基准（真实标定 0.1 kPa/LSB，300 kPa 为典型目标）
# 巡航基线（模块内统一用 _CRUISE_KMH；顶部 CRUISE_KMH 保留兼容别名）
_CRUISE_KMH = CRUISE_KMH


def _active_flags(m: AssetModel, demo: dict, t: float) -> dict:
    """t 时刻各故障激活标志 + 通道强制值（纯事件查询，无物理）。"""
    prof = _profiles()
    inject_at: dict[str, float] = {}
    recover_at: dict[str, float] = {}
    for e in demo["events"]:
        if e["kind"] == "inject":
            inject_at[e["fault"]] = e["t"]
        elif e["kind"] == "recover":
            recover_at[e["fault"]] = e["t"]

    flags = {
        "eb_request": False,
        "eb_ok": True,
        "eb_applied": False,  # EB 真正施加（压力上升 + 减速）
        "doors_open": 0,
        "door_fault_count": 0,
        "heartbeat_ok": True,
        "bus_ok": True,
        "pantograph_ok": True,
        "soc": 80.0,
        "overspeed": False,
        "traction_loss": False,
        "alarms": [],
        "forbidden_speed": None,  # 若故障把速度钉在超限值(超速注入)
    }
    for fk, inj_t in inject_at.items():
        end_t = recover_at.get(fk, demo["duration"])
        if not (inj_t <= t <= end_t):
            continue
        p = prof.get(fk)
        if not p:
            # ⑤ 域特征演示档：未手工建档的故障也给出结构化告警文案
            #（语义真实：名 + 子系统域 + 等级/处置；数值曲线保持巡航基线并明示 derived）
            fd = m.fault(fk) if fk in m.faults_by_key else None
            if fd is not None:
                flags["alarms"].append(_domain_alarm(fd))
            else:
                flags["alarms"].append(f"{_fault_name(m, fk)} 激活")
            continue
        if p.get("eb_request"):
            flags["eb_request"] = True
            flags["eb_ok"] = bool(p.get("eb_ok", True))
        if p.get("eb"):
            flags["eb_request"] = True
            flags["eb_applied"] = True
        if "door_fault_count" in p:
            flags["door_fault_count"] = p["door_fault_count"]
        if "doors_open" in p:
            flags["doors_open"] = p["doors_open"]
        if "heartbeat_ok" in p and not p["heartbeat_ok"]:
            flags["heartbeat_ok"] = False
        if "bus_ok" in p and not p["bus_ok"]:
            flags["bus_ok"] = False
        if "pantograph_ok" in p and not p["pantograph_ok"]:
            flags["pantograph_ok"] = False
        if "soc" in p:
            flags["soc"] = p["soc"]
        if p.get("speed") and fk == "overspeed":
            flags["forbidden_speed"] = p["speed"]
        if fk == "traction_loss":
            flags["traction_loss"] = True
        if fk == "overspeed":
            flags["overspeed"] = True
        if p.get("alarm"):
            flags["alarms"].append(p["alarm"])

    # 处置动作（取当前激活故障里最高优先级）
    actions = [
        e["action"]
        for e in demo["events"]
        if e["kind"] == "action"
        and inject_at.get(e["fault"], float("inf"))
        <= t
        <= recover_at.get(e["fault"], demo["duration"])
        and e["action"]
    ]
    prio = {"none": 0, "warning": 1, "derate": 2, "emergency_brake": 3, "shutdown": 4}
    flags["action"] = max(actions, key=lambda a: prio.get(a, 0)) if actions else "none"
    return flags


def _phys_step(speed: float, flags: dict, dt: float) -> float:
    """一步物理积分（示意）：处置 → 加速/减速。"""
    action = flags["action"]
    # EB 执行失败：虽然请求 EB，但压力不上升 → 列车几乎不减速（教学点：命令≠执行）
    if flags["eb_request"] and not flags["eb_ok"] and not flags["eb_applied"]:
        return max(speed - _EB_FAIL_COAST * dt, 0.0)
    if flags["eb_applied"] or action in ("emergency_brake", "shutdown"):
        return max(speed - _EB_DECEL * dt, 0.0)
    if action == "derate":
        if speed > _DERATE_SPEED:
            # 降级限速：以一定减速率回落到 derate 目标速度
            return max(speed - _DERATE_DECEL * dt, _DERATE_SPEED)
        return speed
    if flags["traction_loss"]:
        # 牵引丢失：无法加速，滑行衰减（示意）
        return max(speed - _COAST_DECEL * dt, 0.0)
    # 正常/仅告警：向巡航加速
    if speed < _CRUISE_KMH:
        return min(speed + _ACCEL_RATE * dt, _CRUISE_KMH)
    return speed


def _walk(m: AssetModel, demo: dict):
    """逐步积分生成 (t, speed, flags)；overspeed 用上升沿一次性置位。"""
    duration = demo["duration"]
    dt = demo["sample_s"]
    speed = 0.0
    prev_flags: dict | None = None
    t = 0.0
    n = max(1, int(math.ceil(duration / dt))) if (duration and dt) else 0
    for _ in range(n + 1):
        t = min(t, duration)
        flags = _active_flags(m, demo, t)
        if flags["overspeed"] and (prev_flags is None or not prev_flags["overspeed"]):
            speed = flags.get("forbidden_speed") or 168.0
        elif t > 0:
            speed = _phys_step(speed, flags, dt)
        yield t, speed, flags
        prev_flags = flags
        t += dt


def build_curve(m: AssetModel, demo: dict) -> list[dict]:
    """生成整条采样曲线（前端一次取回，逐帧播放，不做本地复算）。"""
    pts = [_channel_point(m, demo, t, speed, flags) for t, speed, flags in _walk(m, demo)]
    if pts:
        pts[-1]["t"] = demo["duration"]
    return pts


def _channel_point(m: AssetModel, demo: dict, t: float, speed: float, flags: dict) -> dict:
    """把一个时间点打包成通道帧。"""
    eb = 1 if (flags["eb_request"] and flags["eb_applied"]) else 0
    brake_kpa = _EB_KPA if flags["eb_applied"] else 0.0
    # eb_failure：EB 请求但未执行 → 压力不上（这就是教学点）
    if flags["eb_request"] and not flags["eb_applied"]:
        eb = 1  # 命令有效（指示灯亮）
        brake_kpa = 0.0  # 但执行未落地 → 压力 0
    return {
        "t": round(t, 2),
        "speed_kmh": round(speed, 1),
        "brake_kpa": round(brake_kpa, 1),
        "eb": eb,
        "doors_open": flags["doors_open"],
        "door_fault_count": flags["door_fault_count"],
        "heartbeat_ok": flags["heartbeat_ok"],
        "bus_ok": flags["bus_ok"],
        "pantograph_ok": flags["pantograph_ok"],
        "soc": round(flags["soc"], 1),
        "action": flags["action"],
        "alarms": flags["alarms"],
    }


def channel_at(m: AssetModel, demo: dict, t: float) -> dict:
    """单点通道状态（重放积分到 t，逻辑与 build_curve 完全一致）。"""
    t = min(max(t, 0.0), demo["duration"])
    last = None
    for tt, speed, flags in _walk(m, demo):
        last = (tt, speed, flags)
        if tt >= t:
            break
    if last is None:
        last = (t, 0.0, _active_flags(m, demo, t))
    tt, speed, flags = last
    return _channel_point(m, demo, min(tt, demo["duration"]), speed, flags)
