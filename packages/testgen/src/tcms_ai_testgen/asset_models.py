"""资产数据模型：DBC 报文/信号 + 场景步骤/注入。

供 P2/prompt 消费的统一结构。全部 pydantic——LLM 生成输入的
结构化契约与 models.py 的生成输出契约对称。设计约束：
- 独立于上游 tcms-can-test 包：只解析其文件，不 import 其模块；
- 诚实字段：原始值保留 + 派生可计算字段 + 来源文件名；
- 质量口径（docs/metrics.md）——parse_rate 分母含残缺样本：
  AssetBundle.parse_rate = parsed / (parsed + bad)。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DbcSignal(BaseModel):
    """一条 CAN 信号（DBC ``SG_`` 行）。"""

    name: str = Field(description="信号名")
    start_bit: int = Field(description="起始位（DBC 位序，0 为 LSB）")
    length: int = Field(description="信号位宽")
    is_signed: bool = Field(description="有无符号（@1+/@1-）")
    factor: float = Field(default=1.0, description="缩放因子")
    offset: float = Field(default=0.0, description="偏移")
    minimum: Optional[float] = Field(default=None, description="物理最小值 [min|max]")
    maximum: Optional[float] = Field(default=None, description="物理最大值")
    unit: str = Field(default="", description="单位（如 km/h、V）")
    comment: str = Field(default="", description="多路复用标记（预留，本 DBC 未用）")

    @property
    def raw_range_bits(self) -> int:
        """该信号的物理编码可表达整数值范围（0..2^len-1 或 -2^(len-1)..）。"""
        return 1 << self.length

    @property
    def physical_range(self) -> tuple[Optional[float], Optional[float]]:
        """物理范围：优先 DBC [min|max]，否则按位宽理论值。"""
        if self.minimum is not None or self.maximum is not None:
            return self.minimum, self.maximum
        hi = (1 << self.length) - 1 if not self.is_signed else (1 << (self.length - 1)) - 1
        lo = 0 if not self.is_signed else -(1 << (self.length - 1))
        return lo * self.factor + self.offset, hi * self.factor + self.offset


class DbcValueDescription(BaseModel):
    """VAL_ 枚举文本映射（如 NodeStatus 0->"PowerOff"）。"""

    raw_value: int
    label: str


class DbcMessage(BaseModel):
    """一条 CAN 报文（DBC ``BO_`` 行）。"""

    frame_id: int = Field(description="CAN 仲裁 ID（十进制）")
    name: str
    dlc: int = Field(description="数据长度（字节）")
    transmitter: str = Field(default="", description="发送节点")
    signals: list[DbcSignal] = Field(default_factory=list)
    cycle_ms: Optional[int] = Field(default=None, description="GenMsgCycleTime BA 值（ms），None=事件型")
    send_type: str = Field(default="cyclic", description="GenMsgSendType：cyclic/event")

    @property
    def signal_names(self) -> list[str]:
        """信号名列表（供快速查表 / 下游覆盖统计）。"""
        return [s.name for s in self.signals]

    def signal(self, name: str) -> Optional[DbcSignal]:
        """按名称取信号；不存在返回 None。"""
        for s in self.signals:
            if s.name == name:
                return s
        return None


class DbcFile(BaseModel):
    """一个 DBC 文件的解析结果。"""

    source_path: str = Field(description="源文件路径")
    version: str = Field(default="", description="VERSION 字符串")
    nodes: list[str] = Field(default_factory=list, description="BU_ 节点列表")
    messages: list[DbcMessage] = Field(default_factory=list)
    value_descriptions: dict[int, dict[str, DbcValueDescription]] = Field(
        default_factory=dict, description="frame_id -> {signal_name -> 枚举}"
    )
    #: 行级解析失败样本（格式：行号 -> 原文）。文件级坏文件不算在此。
    raw_line_errors: list[str] = Field(default_factory=list, description="行级解析失败样本（诚实统计）")

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def signal_count(self) -> int:
        return sum(len(m.signals) for m in self.messages)

    def find_message(self, name: str) -> Optional[DbcMessage]:
        """按名字找报文；不存在返回 None。"""
        for m in self.messages:
            if m.name == name:
                return m
        return None


class AssetIndexStats(BaseModel):
    """资产索引的诚实统计（parse_rate 的分母口径见 docs/metrics.md）。"""

    yaml_count: int
    parsed_yaml: int
    bad_yaml: int
    dbc_count: int = 0
    parsed_dbc: int = 0
    bad_dbc: int = 0

    @property
    def parse_rate(self) -> float:
        """结构化解析率 = 成功解析文件数 / 总文件数（含坏文件分母）。"""
        total = self.parsed_yaml + self.bad_yaml + self.parsed_dbc + self.bad_dbc
        if total == 0:
            return 0.0
        return (self.parsed_yaml + self.parsed_dbc) / total


class ScenarioStep(BaseModel):
    """场景 YAML 的一步（支持 3 种写法，loader 已归一化 kind）。"""

    at: float = Field(description="触发时刻（秒）")
    #: 归一化动作：inject/recover；无法识别的事件式 action 记 other（诚实保留）
    kind: Literal["inject", "recover", "other"] = Field(description="归一化动作")
    fault: Optional[str] = Field(default=None, description="故障键（recover 也带）")
    node: Optional[str] = Field(default=None, description="注入节点")
    level: Optional[str] = Field(default=None, description="故障等级 minor/major/critical")
    impact: Optional[str] = Field(default=None, description="故障影响描述")
    expect: Optional[str] = Field(default=None, description="期望处置（derate/emergency_brake…）")
    raw: dict = Field(default_factory=dict, description="原始 YAML 步骤字典（保真）")

    @property
    def is_inject(self) -> bool:
        return self.kind == "inject"


class ScenarioAsset(BaseModel):
    """一个场景 YAML 的解析结果。"""

    source_path: str
    name: Optional[str] = Field(default=None, description="场景中文名（可能缺省）")
    steps: list[ScenarioStep] = Field(default_factory=list)
    #: 无法识别而被跳过的步骤说明（诚实保留，文件仍算 parsed——半结构化）
    skipped_steps: list[str] = Field(default_factory=list, description="未识别步骤的警告")
    raw: dict = Field(default_factory=dict, description="整个 YAML 原始 dict（供下游精确取字段）")

    @property
    def inject_steps(self) -> list[ScenarioStep]:
        return [s for s in self.steps if s.is_inject]

    @property
    def recover_steps(self) -> list[ScenarioStep]:
        return [s for s in self.steps if s.kind == "recover"]


class AssetBundle(BaseModel):
    """一次 load_assets() 的全部输出——P2 与 prompt 的消费入口。"""

    dbc: list[DbcFile]
    scenarios: list[ScenarioAsset]
    stats: AssetIndexStats
    #: 未解析文件（不存在/目录为空/坏文件/编码错误），与 docs 对齐
    bad_files: list[str] = Field(default_factory=list, description="坏文件清单（诚实统计）")

    @property
    def message_total(self) -> int:
        return sum(d.message_count for d in self.dbc)

    @property
    def signal_total(self) -> int:
        return sum(d.signal_count for d in self.dbc)

    def find_message(self, name: str) -> Optional[DbcMessage]:
        """按名字在全部 DBC 中找报文；找不到返回 None。"""
        for d in self.dbc:
            m = d.find_message(name)
            if m is not None:
                return m
        return None

    def find_scenario(self, name: str) -> Optional[ScenarioAsset]:
        """按中文名或文件名（stem）找场景。"""
        for s in self.scenarios:
            if s.name == name or s.source_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].startswith(name):
                return s
        return None


__all__ = [
    "AssetBundle",
    "AssetIndexStats",
    "DbcFile",
    "DbcMessage",
    "DbcSignal",
    "DbcValueDescription",
    "ScenarioAsset",
    "ScenarioStep",
]
