"""列车网络控制（TCMS）CAN 协议定义与常量。

基于打包内 `tcms/tcms.dbc` 的协议封装：
- 报文 ID 常量
- 信号常量（状态枚举）
- DBC 加载与节点信息

DBC 作为包数据随 wheel 分发（pip install 后开箱可用）；`load_database()`
也接受任意外部 DBC 路径（自带协议/车型接入）。
"""

import importlib.resources
from pathlib import Path

import cantools

# 打包内置 DBC（随 wheel 分发）。importlib.resources 兼容常规目录安装与
# zip 安装；load_database() 内部以 files().joinpath().open() 读取（见下），
# DBC_PATH 仅为展示/文档用途（文件系统安装下为真实路径）。
DBC_PATH = Path(str(importlib.resources.files("tcms").joinpath("tcms.dbc")))

# ---- 报文 ID 常量 ----
TCMS_HEARTBEAT = 0x100  # TCMS 心跳（100ms）
VEHICLE_SPEED = 0x200  # 车速（100ms）
TRACTION_BRAKE_HANDLE = 0x300  # 牵引/制动手柄（50ms）
DOOR_CONTROL = 0x400  # 车门控制（100ms）
ALARM_EVENT = 0x500  # 报警事件（事件触发）
PANTOGRAPH_STATUS = 0x600  # 受电弓状态（500ms）
BRAKE_SYSTEM = 0x700  # 制动系统（100ms）
ENERGY_STATUS = 0x780  # 能源/电池状态（500ms）

# ---- 报文名称 ----
MESSAGE_NAMES = {
    TCMS_HEARTBEAT: "TCMS_Heartbeat",
    VEHICLE_SPEED: "VehicleSpeed",
    TRACTION_BRAKE_HANDLE: "TractionBrakeHandle",
    DOOR_CONTROL: "DoorControl",
    ALARM_EVENT: "AlarmEvent",
    PANTOGRAPH_STATUS: "PantographStatus",
    BRAKE_SYSTEM: "BrakeSystem",
    ENERGY_STATUS: "EnergyStatus",
}

# ---- 状态枚举 ----
NODE_STATUS = {0: "PowerOff", 1: "Standby", 2: "Active", 3: "Fault"}
RUN_MODE = {0: "Offline", 1: "Manual", 2: "ATO", 3: "Shunting", 4: "Emergency"}
DIRECTION = {0: "Neutral", 1: "Forward", 2: "Reverse", 3: "Invalid"}
DOOR_STATE = {0: "Closed", 1: "Open", 2: "Fault", 3: "Unknown"}
ALARM_LEVEL = {0: "Info", 1: "Warning", 2: "Severe", 3: "Emergency"}
CHARGE_STATE = {0: "Idle", 1: "Charging", 2: "Discharging", 3: "Fault"}

# ---- 安全阈值 ----
MAX_SPEED_KMH = 200.0  # 信号物理上限
OVERSPEED_LIMIT_KMH = 160.0  # 超速报警阈值
MAX_HANDLE_POSITION = 16  # 手柄级位上限
MAX_ALARM_LEVEL = 3  # 报警等级上限


def load_database(path: Path | str | None = None) -> cantools.database.can.Database:
    """加载 DBC 数据库。

    path 缺省时加载打包内置 `tcms/tcms.dbc`（随 wheel 分发，zip 安装亦可用）；
    传入外部路径可加载用户自有协议数据库（平台化接入点）。
    """
    if path is None:
        resource = importlib.resources.files("tcms").joinpath("tcms.dbc")
        with resource.open("r", encoding="utf-8") as f:
            return cantools.database.load(f)
    return cantools.database.load_file(path)


def encode(db, message_name: str, **signals) -> bytes:
    """编码报文：缺失信号自动补零，返回 8 字节原始数据。"""
    message = db.get_message_by_name(message_name)
    for sig in message.signals:
        signals.setdefault(sig.name, 0)
    return db.encode_message(message_name, signals)


# ---- 多网段归属（Q2-P-A ③-c：MVB 车辆级 / 舒适域 / ETB 骨干）----
# 单一真源 = DBC 的 GenMsgSegment 属性（tcms.dbc BA_ 行）；这里按 frame_id
# 解析该属性供可调度性/负载按网段分组，避免在代码里手抄第二份映射。


def load_segment_map() -> dict[int, str]:
    """从打包内置 DBC 解析 GenMsgSegment → {frame_id: segment}。

    段取值：vehicle（车辆级/安全高频）· comfort（舒适域）· backbone（骨干网）；
    事件型/未标注帧不出现（调用方按需回退 ""）。
    """
    import re

    seg: dict[int, str] = {}
    with DBC_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'BA_ "GenMsgSegment" BO_ (\d+) "([A-Za-z_]+)";', line.strip())
            if m:
                seg[int(m.group(1))] = m.group(2)
    return seg


def segment_of(frame_id: int) -> str:
    """frame_id → 网段（未标注/事件帧返回 ''）。"""
    return load_segment_map().get(frame_id, "")
