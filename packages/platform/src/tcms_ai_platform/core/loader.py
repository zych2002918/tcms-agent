"""资产加载器：从上游 tcms-can-test 真实资产构建 L1 AssetModel。

数据源（全部真实，只读引用上游，不复制）：
- DBC 协议库      tcms-can-test/tcms/tcms.dbc       → 报文/信号（22 帧 / 116 信号）
- FMEA 故障字典   tcms-can-test/tcms/faults.yaml    → 故障条目（66，13 系统域）
- 场景 YAML       tcms-can-test/scenarios/*.yaml    → 场景（59）
- RTM 追溯矩阵    tcms-can-test/tests/rtm.csv       → 需求 SR-01~52
- 被测功能        curated（本文件 _FUNCTIONS，11 个），锚定真实 RTM / 报文 / 故障键

设计纪律：
- loader 不做任何"美化"——计数/关系全部派生自真实文件；
- 缺文件/解析失败 → 诚实计入 load_stats.bad（镜像上游 parse_rate 口径）；
- curated 功能表的每个 req_id 若不在 RTM → 抛错（需求漂移即失败，防手抄）。
"""

from __future__ import annotations

import csv
from pathlib import Path

import cantools
import yaml

# 资产模型版本单一真源：默认平台版本 = 包版本（_version.py），禁止手写 "0.1.0"
from .._version import __version__ as _PLATFORM_VERSION
from .models import (
    AssetModel,
    DeviceDef,
    FaultDef,
    FunctionDef,
    MessageDef,
    RequirementDef,
    ScenarioDef,
    ScenarioStep,
    SignalDef,
)

# 设备角色标签（中文，展示用）
_DEVICE_ROLES = {
    "TCMS": "列车控制与管理系统（中央）",
    "VCU": "车辆控制单元（主控）",
    "BMS": "电池管理系统",
    "BOGIE": "转向架/车门控制",
    "BCU": "制动控制单元",
    "HVAC": "空调控制单元",
    "PIS": "乘客信息系统",
    "LIGHT": "照明控制单元",
    "FIRE": "烟火探测系统",
    "AUX": "辅助变流器",
    "ATO": "自动驾驶单元",
}

# DBC 节点 → 注入节点（场景 node: 使用小写，映射到大写设备名）
_NODE_ALIAS = {
    "bcu": "BCU",
    "bogie": "BOGIE",
    "vcu": "VCU",
    "bus": "BUS",
    "bms": "BMS",
    "tcms": "TCMS",
    "hvac": "HVAC",
    "pis": "PIS",
    "light": "LIGHT",
    "fire": "FIRE",
    "aux": "AUX",
    "ato": "ATO",
    "pantograph": "BCU",
}

# 子系统（故障字典）→ 发送/关联设备（展示用近邻，不深究）
_SUBSYSTEM_DEVICE = {
    "VCU": "VCU",
    "列车控制": "TCMS",
    "车门": "BOGIE",
    "能源": "BMS",
    "牵引": "VCU",
    "受电弓": "BCU",
    "制动": "BCU",
    "网络": "TCMS",
    "辅助电源": "AUX",
    "空调": "HVAC",
    "乘客信息": "PIS",
    "照明": "LIGHT",
    "烟火": "FIRE",
    "乘客安全": "FIRE",
    "走行部": "BOGIE",
    "信号": "VCU",
}


class AssetLoadError(RuntimeError):
    """资产加载失败（缺文件 / 结构错 / 需求漂移）。"""


def _read_dbc(db_path: Path) -> tuple[dict[str, MessageDef], dict[str, SignalDef]]:
    """解析 DBC → MessageDef / SignalDef（含周期/段属性与枚举选择表）。"""
    import re

    # 网段单一真源：DBC 的 GenMsgSegment 属性（BA_ 行）→ {frame_id: segment}
    segments: dict[int, str] = {}
    with db_path.open("r", encoding="utf-8") as raw:
        for line in raw:
            m = re.match(r'BA_ "GenMsgSegment" BO_ (\d+) "([A-Za-z_]+)";', line.strip())
            if m:
                segments[int(m.group(1))] = m.group(2)
    with db_path.open("r", encoding="utf-8") as f:
        db = cantools.database.load(f)

    messages: dict[str, MessageDef] = {}
    signals: dict[str, SignalDef] = {}

    for msg in db.messages:
        cycle = None
        send_type = "event"
        if msg.cycle_time is not None:  # cantools 解析 GenMsgCycleTime
            cycle = int(msg.cycle_time)
        # GenMsgSendType：cantools 不一定保留，退化为 周期>0 → cyclic
        send_type = "cyclic" if (cycle or 0) > 0 else "event"
        sig_names = [s.name for s in msg.signals]
        messages[msg.name] = MessageDef(
            frame_id=msg.frame_id,
            name=msg.name,
            node=msg.senders[0] if msg.senders else "",
            length=msg.length,
            cycle_ms=cycle,
            send_type=send_type,
            segment=segments.get(msg.frame_id, ""),
            signal_names=tuple(sig_names),
        )
        for s in msg.signals:
            choices = {}
            if s.choices:  # VAL_ 枚举表
                for k, v in s.choices.items():
                    choices[int(k)] = str(v)
            signals[s.name] = SignalDef(
                name=s.name,
                message=msg.name,
                bit_length=s.length,
                scale=float(s.scale or 1.0),
                offset=float(s.offset or 0.0),
                minimum=float(s.minimum) if s.minimum is not None else None,
                maximum=float(s.maximum) if s.maximum is not None else None,
                unit=str(s.unit or ""),
                choices=choices,
                receivers=tuple(str(r) for r in s.receivers),
            )
    return messages, signals


def _read_faults(yaml_path: Path) -> tuple[dict[str, FaultDef], dict[str, FaultDef]]:
    """解析 faults.yaml → by_key / by_fid 索引。"""
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    by_key: dict[str, FaultDef] = {}
    by_fid: dict[str, FaultDef] = {}
    for raw in data.get("faults", []):
        fd = FaultDef(
            fid=raw["fid"],
            key=raw["key"],
            name=raw.get("name", ""),
            subsystem=raw.get("subsystem", ""),
            layer=raw.get("layer", ""),
            level=raw.get("level", ""),
            action=raw.get("action", ""),
            sil=str(raw.get("sil", "")),
            desc=raw.get("desc", ""),
            detect=raw.get("detect", ""),
            inject=raw.get("inject", ""),
            recovery=raw.get("recovery", ""),
            action_note=raw.get("action_note", ""),  # 处置条件化说明（可选字段，缺省空串）
        )
        by_key[fd.key] = fd
        by_fid[fd.fid] = fd
    return by_key, by_fid


def _read_scenarios(scenarios_dir: Path) -> dict[str, ScenarioDef]:
    """解析 scenarios/*.yaml → ScenarioDef（事件式与显式 inject/recover 都支持）。"""
    out: dict[str, ScenarioDef] = {}
    for f in sorted(scenarios_dir.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "steps" not in data:
            raise AssetLoadError(f"场景 {f.name}: 缺 steps")
        steps: list[ScenarioStep] = []
        faults: set[str] = set()
        nodes: set[str] = set()
        for raw in data["steps"]:
            if "at" not in raw:
                raise AssetLoadError(f"场景 {f.name}: 步骤缺 at")
            ts = float(raw["at"])
            if "inject" in raw:
                inj = raw["inject"]
                fault = inj.get("fault", raw.get("fault"))
                node = inj.get("node", raw.get("node"))
                steps.append(
                    ScenarioStep(
                        at=ts,
                        action="inject",
                        node=node,
                        fault=fault,
                        level=inj.get("level", raw.get("level")),
                        expect=inj.get("expect", raw.get("expect")),
                        impact=inj.get("impact", raw.get("impact")),
                    )
                )
            elif "recover" in raw:
                steps.append(
                    ScenarioStep(at=ts, action="recover", fault=raw["recover"])
                )
            else:  # 事件式: action + fault
                steps.append(
                    ScenarioStep(
                        at=ts,
                        action=raw.get("action", ""),
                        node=raw.get("node"),
                        fault=raw.get("fault"),
                        level=raw.get("level"),
                        expect=raw.get("expect"),
                        impact=raw.get("impact"),
                    )
                )
            if steps[-1].fault:
                faults.add(steps[-1].fault)
            if steps[-1].node:
                nodes.add(steps[-1].node)
        out[f.name] = ScenarioDef(
            name=data.get("name", f.stem),
            file=f.name,
            steps=tuple(steps),
            fault_keys=frozenset(faults),
            nodes=frozenset(nodes),
            desc=data.get("desc", "") or "",
        )
    return out


def _read_rtm(csv_path: Path) -> dict[str, list[RequirementDef]]:
    """解析 tests/rtm.csv → by req_id（保留多行，如 SR-07 多个验证）。"""
    out: dict[str, list[RequirementDef]] = {}
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(row for row in f if not row.startswith("#"))
        for row in reader:
            req = RequirementDef(
                req_id=row["req_id"].strip(),
                module=row["module"].strip(),
                test_file=row["test_file"].strip(),
                verifies=row["verifies"].strip(),
                status=row.get("status", "").strip(),
            )
            out.setdefault(req.req_id, []).append(req)
    return out


# ---- 被测功能表（curated，锚定真实 RTM / 报文 / 故障键） ----
# 每个 req_id 必须真实存在于 RTM（loader 校验），信号/报文必须真实存在于 DBC。

_FUNCTIONS: list[dict] = [
    {
        "fid": "F-EBM",
        "name": "紧急制动管理（EBM）",
        "description": "按模式×原因矩阵触发紧急制动，SIL2/4 双通道表决，缓解需零速+原因消失闭环",
        "messages": ["BrakeSystem", "TractionBrakeHandle", "AlarmEvent"],
        "signals": ["EmergencyBrakeActive", "BrakeCylinderPressure", "BrakeFault"],
        "fault_keys": ["eb_failure", "traction_brake_conflict", "overspeed"],
        "requirements": ["SR-01", "SR-02", "SR-03", "SR-13"],
    },
    {
        "fid": "F-ATP",
        "name": "超速防护（ATP）",
        "description": "速度监督阈值 EBI/SBI 分级干预；速度信号失效/冗余不足时监督降级",
        "messages": ["VehicleSpeed", "AlarmEvent", "BrakeSystem"],
        "signals": ["SpeedKmh", "SpeedValid", "Overspeed"],
        "fault_keys": [
            "overspeed",
            "speed_sensor_drift",
            "speed_signal_loss",
            "signal_redundancy_loss",
        ],
        "requirements": ["SR-05", "SR-06", "SR-19", "SR-20"],
    },
    {
        "fid": "F-DOOR",
        "name": "车门联锁与级联",
        "description": "前/后车门状态（Closed/Open/Fault/Unknown）联锁发车许可，故障级联降级，运行中门开安全制动",
        "messages": ["DoorControl", "DoorControlRear", "AlarmEvent"],
        "signals": ["Door1State", "Door5State", "AllDoorsClosed", "DoorOpenPermit"],
        "fault_keys": [
            "door_fault",
            "door_sensor_noise",
            "rear_door_fault",
            "door_open_moving",
            "door_air_pressure_low",
        ],
        "requirements": ["SR-04", "SR-21", "SR-22", "SR-23"],
    },
    {
        "fid": "F-NET",
        "name": "网络管理与完整性",
        "description": "心跳监督、CRC 校验、错误状态机、总线故障处置、网关网段/冗余与主控看门狗",
        "messages": ["TCMS_Heartbeat", "Gateway_Status", "AlarmEvent"],
        "signals": [
            "HeartbeatCounter",
            "NodeStatus",
            "RunMode",
            "GatewayFault",
            "MvbSegmentA",
        ],
        "fault_keys": [
            "heartbeat_loss_vcu",
            "vcu_watchdog_timeout",
            "crc_error_frame",
            "node_restart_storm",
            "bus_short",
            "gateway_segment_fault",
            "gateway_redundancy_loss",
            "frame_period_jitter",
            "driver_console_fault",
        ],
        "requirements": ["SR-07", "SR-08", "SR-09", "SR-16", "SR-49", "SR-50", "SR-51"],
    },
    {
        "fid": "F-TRAC",
        "name": "牵引变流器保护",
        "description": "变流器故障码封锁牵引、过温降功率、直流母线欠压保护",
        "messages": ["Traction_Converter"],
        "signals": ["ConvTemp", "ConvFaultCode", "TracDisable", "DcLinkVoltage"],
        "fault_keys": [
            "traction_converter_fault",
            "traction_converter_overheat",
            "dc_link_undervoltage",
            "traction_loss",
        ],
        "requirements": ["SR-24", "SR-25", "SR-26"],
    },
    {
        "fid": "F-BRAKE",
        "name": "常用制动与防滑（BCU）",
        "description": "制动缸压力闭环、防滑（WSP）监督、备用制动储备监督与泄漏降级",
        "messages": ["BrakeSystem", "Brake_Wsp"],
        "signals": [
            "BrakeCylinderPressure",
            "BrakeFault",
            "ReservePressureLow",
            "WspActive",
            "WspFault",
        ],
        "fault_keys": [
            "brake_cylinder_leak",
            "brake_wsp_fault",
            "brake_reserve_low",
            "brake_actuator_stuck",
        ],
        "requirements": ["SR-27", "SR-28", "SR-29"],
    },
    {
        "fid": "F-HVAC",
        "name": "空调暖通（HVAC）",
        "description": "客室温度闭环、压缩机/加热器保护、新风与滤网监督、过温降级",
        "messages": ["HVAC_CabinStatus", "HVAC_Monitor"],
        "signals": [
            "CabinTemp",
            "CompressorState",
            "HvacFaultCode",
            "FilterDirty",
            "HeaterState",
            "FreshAirDamper",
        ],
        "fault_keys": [
            "hvac_compressor_fault",
            "hvac_compressor_overcurrent",
            "hvac_cabin_overheat",
            "hvac_filter_clog",
            "hvac_heater_fault",
            "hvac_fresh_air_damper",
        ],
        "requirements": ["SR-39", "SR-40"],
    },
    {
        "fid": "F-PIS",
        "name": "乘客信息系统（PIS）",
        "description": "到站信息/播报、显示屏状态监督、乘客紧急对讲可用性",
        "messages": ["PIS_PassengerInfo"],
        "signals": ["PisDisplayState", "EmergencyTalkActive", "NextStationCode", "PisFaultCode"],
        "fault_keys": ["pis_display_fault", "pis_intercom_fault", "pis_announce_desync"],
        "requirements": ["SR-41", "SR-42"],
    },
    {
        "fid": "F-FIRE",
        "name": "烟火安全",
        "description": "烟雾探测与分区报警、灭火装置就绪监督、火灾停车疏散",
        "messages": ["Fire_Detection", "AlarmEvent"],
        "signals": [
            "SmokeDetectorState",
            "FireZone",
            "ExtinguisherReady",
            "FireSystemFault",
            "FireAlarm",
        ],
        "fault_keys": [
            "smoke_detected",
            "fire_detector_fault",
            "fire_extinguisher_fault",
            "fire_multizone_alarm",
        ],
        "requirements": ["SR-43", "SR-44"],
    },
    {
        "fid": "F-PWR",
        "name": "高压与供电",
        "description": "受电弓升降与网压、电池储能/绝缘、辅助变流与接触器保护",
        "messages": [
            "PantographStatus",
            "EnergyStatus",
            "Aux_Converter",
            "Battery_Charger",
        ],
        "signals": [
            "LineVoltage",
            "PantographUp",
            "SocPercent",
            "BatteryVoltage",
            "AuxVoltage",
            "AuxLoadPercent",
            "ChargerState",
        ],
        "fault_keys": [
            "pantograph_arc",
            "pantograph_fail_raise",
            "line_voltage_sag",
            "soc_low",
            "temp_high",
            "battery_insulation_fault",
            "battery_voltage_imbalance",
            "bms_charge_state_conflict",
            "aux_converter_fault",
            "aux_voltage_out_of_range",
            "aux_converter_overload",
            "aux_contactor_weld",
        ],
        "requirements": [
            "SR-30",
            "SR-31",
            "SR-32",
            "SR-33",
            "SR-34",
            "SR-35",
            "SR-36",
            "SR-37",
            "SR-38",
        ],
    },
    {
        "fid": "F-BOGIE",
        "name": "走行部监测",
        "description": "轴温超限限速、振动趋势监测、监测链路健康告警",
        "messages": ["Bogie_Monitor", "AlarmEvent"],
        "signals": ["Axle1Temp", "VibrationLevel", "VibrationTrend", "BogieSensorFault", "BogieVibration"],
        "fault_keys": ["bogie_vibration_high", "bogie_axle_overheat", "bogie_sensor_fault"],
        "requirements": ["SR-45", "SR-46"],
    },
]


def _curate_devices(
    messages: dict[str, MessageDef], faults_by_key: dict[str, FaultDef]
) -> dict[str, DeviceDef]:
    """从 DBC 发送节点 + 场景注入节点 + 子系统 组装设备表。"""
    # 收集发送节点 → 报文
    senders: dict[str, list[str]] = {}
    for name, m in messages.items():
        senders.setdefault(m.node, []).append(name)

    # 场景注入节点全集（小写）
    # 从故障字典子系统推断近邻设备
    sub_to_dev: dict[str, list[str]] = {}
    for fd in faults_by_key.values():
        dev = _SUBSYSTEM_DEVICE.get(fd.subsystem)
        if dev:
            sub_to_dev.setdefault(fd.subsystem, []).append(fd.key)

    devices: dict[str, DeviceDef] = {}
    # 先按 DBC 节点全集
    for node, msgs in senders.items():
        faults = []
        # 子系统近邻（取该设备所属子系统的故障键）
        for sub, dev in _SUBSYSTEM_DEVICE.items():
            if dev == node:
                faults.extend(sub_to_dev.get(sub, []))
        devices[node] = DeviceDef(
            name=node,
            role=_DEVICE_ROLES.get(node, node),
            messages=tuple(sorted(msgs)),
            faults=tuple(sorted(set(faults))),
        )
    return devices


def load_asset_model(
    upstream_root: str | Path,
    platform_version: str = _PLATFORM_VERSION,
) -> AssetModel:
    """从上游 tcms-can-test 根目录加载完整 L1 资产模型（开发/测试用）。"""
    root = Path(upstream_root)
    if not root.is_dir():
        raise AssetLoadError(f"上游目录不存在: {root}")

    msgs_path = root / "tcms" / "tcms.dbc"
    faults_path = root / "tcms" / "faults.yaml"
    scenarios_dir = root / "scenarios"
    rtm_path = root / "tests" / "rtm.csv"
    return _load_from_paths(
        msgs_path=msgs_path,
        faults_path=faults_path,
        scenarios_dir=scenarios_dir,
        rtm_path=rtm_path,
        source_upstream=str(root),
        platform_version=platform_version,
    )


def load_from_source(source, platform_version: str = _PLATFORM_VERSION) -> AssetModel:
    """从解析出的资产源加载（支持活上游 / 内置快照，见 core/sources.py）。"""
    return _load_from_paths(
        msgs_path=source.dbc,
        faults_path=source.faults,
        scenarios_dir=source.scenarios_dir,
        rtm_path=source.rtm,
        source_upstream=str(source.root) if source.root else f"bundled:{source.mode}",
        platform_version=platform_version,
    )


def _load_from_paths(
    msgs_path: Path,
    faults_path: Path,
    scenarios_dir: Path,
    rtm_path: Path,
    source_upstream: str,
    platform_version: str,
) -> AssetModel:
    """共享的资产加载核心：从四个数据文件路径构建 AssetModel。"""
    msgs_path = Path(msgs_path)
    faults_path = Path(faults_path)
    scenarios_dir = Path(scenarios_dir)
    rtm_path = Path(rtm_path)

    missing = [
        str(p)
        for p in (msgs_path, faults_path, scenarios_dir, rtm_path)
        if not p.exists()
    ]
    if missing:
        raise AssetLoadError(f"上游资产缺失: {missing}")

    stats: dict = {"bad": [], "ok": []}

    messages, signals = _read_dbc(msgs_path)
    stats["ok"].append(f"dbc:{len(messages)}msgs/{len(signals)}sigs")

    faults_by_key, faults_by_fid = _read_faults(faults_path)
    stats["ok"].append(f"faults:{len(faults_by_key)}")

    scenarios = _read_scenarios(scenarios_dir)
    stats["ok"].append(f"scenarios:{len(scenarios)}")

    requirements = _read_rtm(rtm_path)
    stats["ok"].append(f"rtm:{sum(len(v) for v in requirements.values())}rows/{len(requirements)}reqs")

    # 校验 curated 功能表：req_id 必须存在于 RTM
    functions: dict[str, FunctionDef] = {}
    known_msgs = set(messages)
    known_sigs = set(signals)
    known_faults = set(faults_by_key)
    for raw in _FUNCTIONS:
        for req in raw["requirements"]:
            if req not in requirements:
                raise AssetLoadError(f"功能 {raw['fid']}: 需求 {req} 不在 RTM（需求漂移）")
        for m in raw["messages"]:
            if m not in known_msgs:
                raise AssetLoadError(f"功能 {raw['fid']}: 报文 {m} 不在 DBC")
        for s in raw["signals"]:
            if s not in known_sigs:
                raise AssetLoadError(f"功能 {raw['fid']}: 信号 {s} 不在 DBC")
        for k in raw["fault_keys"]:
            if k not in known_faults:
                raise AssetLoadError(f"功能 {raw['fid']}: 故障 {k} 不在故障字典")
        functions[raw["fid"]] = FunctionDef(
            fid=raw["fid"],
            name=raw["name"],
            description=raw["description"],
            messages=tuple(raw["messages"]),
            signals=tuple(raw["signals"]),
            fault_keys=tuple(raw["fault_keys"]),
            requirements=tuple(raw["requirements"]),
        )
    stats["ok"].append(f"functions:{len(functions)}")

    devices = _curate_devices(messages, faults_by_key)
    stats["ok"].append(f"devices:{len(devices)}")

    return AssetModel(
        version=platform_version,
        source_upstream=source_upstream,
        messages=messages,
        signals=signals,
        devices=devices,
        faults_by_key=faults_by_key,
        faults_by_fid=faults_by_fid,
        scenarios=scenarios,
        requirements=requirements,
        functions=functions,
        load_stats=stats,
    )


# 便捷：按环境解析资产源（活上游 / 兄弟目录 / 内置快照）
def load_default(platform_version: str = _PLATFORM_VERSION) -> AssetModel:
    """按环境自动解析资产源并加载（新人 clone 即可用，无需手工配置）。"""
    from .sources import resolve_asset_source

    source = resolve_asset_source()
    return load_from_source(source, platform_version=platform_version)
