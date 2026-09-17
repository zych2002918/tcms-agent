"""L1 资产模型：列车视角（Consist view）的领域 schema。

设计原则（继承上游 tcms-can-test 纪律）：
- **从真实资产派生**：报文/信号 ← DBC（cantools 解析，含周期/发送类型/枚举），
  故障 ← faults.yaml（203 键 FMEA），场景 ← scenarios/*.yaml（104 个），
  需求 ← tests/rtm.csv（SR-01~52 追溯矩阵）。
- **数字机器自证**：所有 count/stats 由加载结果派生，禁止手抄。
- **列车视角而非总线视角**：Device 挂报文、Function 聚合「报文+信号+故障+需求」，
  让 AI 与 UI 能回答"这条信号属于哪个系统、服务哪条安全需求"。

schema 字段对齐上游真实数据形状（见 core/loader.py 解析）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 协议层：信号 / 报文（DBC 派生）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalDef:
    """DBC 信号定义（对齐 cantools 真实解析值）。"""

    name: str
    message: str  # 所属报文名
    bit_length: int
    scale: float
    offset: float
    minimum: float | None
    maximum: float | None
    unit: str
    # 枚举选择表：{原始值: 文本}（VAL_ 定义，如 DoorState 0->Closed）
    choices: dict[int, str] = field(default_factory=dict)
    # 接收节点（DBC SG_ 行尾注释列）
    receivers: tuple[str, ...] = ()


@dataclass(frozen=True)
class MessageDef:
    """DBC 报文定义（含 GenMsgCycleTime / GenMsgSendType / GenMsgSegment 属性）。"""

    frame_id: int
    name: str
    node: str  # 发送节点（BO_ 行节点）
    length: int  # 字节
    cycle_ms: int | None  # GenMsgCycleTime（0 = 事件型无固定周期）
    send_type: str  # cyclic / event
    segment: str = ""  # 网段（GenMsgSegment：vehicle/comfort/backbone；''=事件/未标注）
    signal_names: tuple[str, ...] = ()  # 有序信号名


# ---------------------------------------------------------------------------
# 设备 / 列车结构（列车视角）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceDef:
    """列车上的一个设备/节点（含其发送的报文与角色标签）。"""

    name: str  # 规范大写名（VCU / BCU / BMS / BOGIE / TCMS）
    role: str  # 中文角色标签（主控单元 / 制动控制单元 / …）
    messages: tuple[str, ...] = ()  # 该设备发送的报文名
    faults: tuple[str, ...] = ()  # 该设备关联的故障键（场景注入节点派生）


# ---------------------------------------------------------------------------
# 故障 / 场景（FMEA + 声明式场景）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaultDef:
    """故障字典条目（faults.yaml 一条，字段与上游 REQUIRED_FIELDS 对齐）。"""

    fid: str  # F-TCMS-xxx
    key: str  # 英文键（场景 YAML fault 字段）
    name: str  # 中文名
    subsystem: str  # 所属子系统（牵引/制动/车门/能源/受电弓/VCU/网络/信号）
    layer: str  # application/signal/frame/bus/node
    level: str  # info/minor/major/critical
    action: str  # none/warning/derate/emergency_brake/shutdown
    sil: str  # "0".."4"
    desc: str
    detect: str
    inject: str
    recovery: str
    action_note: str = ""  # 处置条件化说明（action_note：单一默认处置背后的"取决于原因"语义）


@dataclass(frozen=True)
class ScenarioStep:
    """场景单步（事件式 YAML：inject/recover + at 时间戳）。"""

    at: float
    action: str  # inject / recover
    node: str | None = None
    fault: str | None = None
    level: str | None = None
    expect: str | None = None
    impact: str | None = None


@dataclass(frozen=True)
class ScenarioDef:
    """声明式故障场景（scenarios/*.yaml 一个文件）。"""

    name: str
    file: str  # 文件名（唯一 id）
    steps: tuple[ScenarioStep, ...] = ()
    fault_keys: frozenset[str] = frozenset()
    nodes: frozenset[str] = frozenset()
    desc: str = ""  # 简短释义（场景 YAML desc 可选字段；无则空）


# ---------------------------------------------------------------------------
# 需求追溯 / 被测功能（列车视角的"测试对象"聚合）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementDef:
    """RTM 追溯矩阵一行（SR → 实现模块 → 验证测试 → 覆盖行为 → 状态）。"""

    req_id: str  # SR-01
    module: str  # tcms/ebm.py
    test_file: str  # tests/test_ebm.py
    verifies: str  # 覆盖行为描述（人类可读）
    status: str  # covered


@dataclass(frozen=True)
class FunctionDef:
    """被测功能（列车视角聚合）：把"测报文"升维为"测功能"。

    显式定义（curated，锚定真实 RTM 需求与真实报文/故障键），
    加载时 loader 校验每个 req_id 确实存在于 RTM —— 需求漂移即失败。
    """

    fid: str  # F-EBM / F-ATP / ...
    name: str  # 中文功能名
    description: str  # 一句话（含边界/模式语义）
    messages: tuple[str, ...]  # 相关真实报文
    signals: tuple[str, ...]  # 相关真实信号
    fault_keys: tuple[str, ...]  # 相关真实故障键
    requirements: tuple[str, ...]  # 相关 RTM 需求 id（loader 校验存在）


# ---------------------------------------------------------------------------
# 资产总集
# ---------------------------------------------------------------------------


@dataclass
class AssetModel:
    """L1 资产总集：加载后的只读查询视图（计数全部派生）。"""

    version: str  # 平台版本
    source_upstream: str  # 上游 tcms-can-test 根目录
    # --- 协议 ---
    messages: dict[str, MessageDef] = field(default_factory=dict)  # by name
    signals: dict[str, SignalDef] = field(default_factory=dict)  # by name
    # --- 列车结构 ---
    devices: dict[str, DeviceDef] = field(default_factory=dict)  # by name
    # --- 故障/场景 ---
    faults_by_key: dict[str, FaultDef] = field(default_factory=dict)
    faults_by_fid: dict[str, FaultDef] = field(default_factory=dict)
    scenarios: dict[str, ScenarioDef] = field(default_factory=dict)  # by file
    # --- 需求/功能 ---
    requirements: dict[str, list[RequirementDef]] = field(default_factory=dict)  # by req_id
    functions: dict[str, FunctionDef] = field(default_factory=dict)  # by fid
    # --- 加载统计（诚实派生） ---
    load_stats: dict = field(default_factory=dict)

    # ---- 查询 ----

    def message(self, name: str) -> MessageDef:
        return self.messages[name]

    def signal(self, name: str) -> SignalDef:
        return self.signals[name]

    def fault(self, key: str) -> FaultDef:
        return self.faults_by_key[key]

    def scenario(self, file: str) -> ScenarioDef:
        return self.scenarios[file]

    def function(self, fid: str) -> FunctionDef:
        return self.functions[fid]

    # ---- 汇总 ----

    def stats(self) -> dict:
        """计数全部派生自真实加载结果（机器自证）。"""
        return {
            "version": self.version,
            "source_upstream": self.source_upstream,
            "messages": len(self.messages),
            "signals": len(self.signals),
            "devices": len(self.devices),
            "faults": len(self.faults_by_key),
            "scenarios": len(self.scenarios),
            "requirements": sum(len(v) for v in self.requirements.values()),
            "req_ids": len(self.requirements),
            "functions": len(self.functions),
        }
