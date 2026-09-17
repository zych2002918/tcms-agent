"""执行意图（Execution Intent）——连接生成与真实执行的机器可读 DSL。

背景（第一性原理）：本项目的证据链断点是「自然语言用例 ↔ 真实可执行代码」
的鸿沟（P2 实证：mock 生成用例几乎全部无法编译成真实 pytest）。
解决方案：生成器（mock 与真实 LLM 同一契约）额外输出**受约束执行意图**——
白名单原语，只表达上游 tcms-can-test 已实测支持的注入/断言面，禁止自由代码。
这样「需求 → 生成 → 编译 → 真实执行 → 量化报告」全链路可复现、数字可信。

白名单对齐原则（不重复造轮子）：
- 故障语义复用上游 faultlevel.FAULTS 的故障键 + 处置动作
  （none/warning/derate/emergency_brake，模式敏感）；
- 信号注入复用上游 simulator.set_* 白名单；
- 信号断言复用上游 parser.collect 解码结果，枚举值 = DBC VAL_ 文本
  （Door2State=='Fault' 而非 2——P2 实测教训）。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

#: 允许的原语操作名（白名单；编译层只认这些）
SETUP_OPS: frozenset[str] = frozenset(
    {
        # 信号注入（simulator 白名单）
        "set_speed",
        "set_handle",
        "set_door_state",
        "stop_message",
        "send_alarm",
        # 编码边界（db.encode_message + EncodeError）
        "expect_encode_error",
        # 故障注入（faultlevel 白名单键）
        "inject_fault",
        "recover_fault",
    }
)

ASSERT_OPS: frozenset[str] = frozenset(
    {
        "expect_signal",  # 解码后信号值断言（枚举文本或数值）
        "expect_no_frames",  # 指定报文在窗口内 0 帧（丢报）
        "expect_encode_error",  # db.encode_message 抛 EncodeError
        "expect_encode_ok",  # db.encode_message 成功且长度 8
        "expect_action",  # 故障处置动作（对齐 faultlevel.action_for）
        "expect_frames_at_least",  # 窗口内帧数下限（周期正常）
    }
)

#: execution.kind 分类
EXEC_KINDS: tuple[str, ...] = (
    "encode_bound",  # 编码边界（信号越界拒绝 / 上限接受）
    "simulate_inject",  # 仿真注入 + 总线解码断言
    "fault_scenario",  # 故障场景（对齐上游 FaultScenario 语义）
)


class ExecStep(BaseModel):
    """一条执行原语（setup 或 expect）。"""

    op: str = Field(description=f"白名单原语：setup∈{sorted(SETUP_OPS)} expect∈{sorted(ASSERT_OPS)}")
    args: dict[str, Any] = Field(default_factory=dict, description="原语参数（白名单键）")

    @field_validator("op")
    @classmethod
    def _op_in_union(cls, v: str) -> str:
        if v not in SETUP_OPS and v not in ASSERT_OPS:
            raise ValueError(f"未知原语 {v!r}（白名单见 SETUP_OPS/ASSERT_OPS）")
        return v


class ExecutionIntent(BaseModel):
    """生成用例的机器可读执行意图（可选字段；缺失则该用例走自然语言兜底）。"""

    kind: Literal["encode_bound", "simulate_inject", "fault_scenario"] = "simulate_inject"
    #: 前置注入序列（按序执行）
    setup: list[ExecStep] = Field(default_factory=list)
    #: 期望断言（全部通过才 PASS）
    expect: list[ExecStep] = Field(default_factory=list)
    #: 等待/采集窗口秒（simulate 类；默认 0.3 采集 0.3）
    wait_ms: int = Field(default=150, ge=0, le=5000)
    collect_ms: int = Field(default=300, ge=10, le=5000)
    #: 故障场景专用：node/fault（对齐上游 FaultScenario）
    node: Optional[str] = Field(default=None, description="注入节点（fault_scenario）")
    fault: Optional[str] = Field(default=None, description="故障键（faultlevel 白名单）")
    recover_after_ms: Optional[int] = Field(default=None, description="注入后多久恢复（故障场景时序）")

    @field_validator("fault")
    @classmethod
    def _fault_nonempty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("fault 不能为空串")
        return v

    @property
    def has_asserts(self) -> bool:
        return bool(self.expect)

    def is_executable(self) -> bool:
        """最小可执行判定：有断言（fault_scenario 需 fault）。"""
        if self.kind == "fault_scenario":
            return bool(self.fault and self.expect)
        return bool(self.expect) and all(e.op in ASSERT_OPS for e in self.expect)


# 向后兼容：GeneratedCase.execution 字段由 models.py 引用
__all__ = [
    "ASSERT_OPS",
    "EXEC_KINDS",
    "ExecStep",
    "ExecutionIntent",
    "SETUP_OPS",
]
