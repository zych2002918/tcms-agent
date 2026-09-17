"""数据模型：生成请求、生成用例、质量报告。

所有模型用 pydantic 做结构化约束——LLM 输出的自由文本必须能严格
解析成这些结构，否则判为「生成失败」，这是质量评估的第一道硬闸。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from tcms_ai_testgen.execution import ExecutionIntent


class GenRequest(BaseModel):
    """一次用例生成请求的输入。"""

    #: 被测对象描述（如：列车 TCMS 紧急制动 EBM 逻辑）
    target: str = Field(description="被测对象/模块描述")
    #: 自然语言需求或安全需求（SR 编号文本）
    requirements: list[str] = Field(default_factory=list)
    #: 关键边界/场景提示（可选，增强生成针对性）
    hints: list[str] = Field(default_factory=list)
    #: 期望生成的用例数量
    num_cases: int = Field(default=5, ge=1, le=50)
    #: 用例所属层级标记（smoke/safety/regression）
    tier: Literal["smoke", "safety", "regression"] = "smoke"


class GeneratedCase(BaseModel):
    """LLM 生成的一个测试用例（结构化）。"""

    #: 用例标题/ID 语义名（如 test_ebm_trigger_on_overspeed）
    name: str = Field(description="用例名（pytest 函数名风格）")
    #: 用例目的（对应哪条需求/哪个场景）
    purpose: str = Field(description="用例目的")
    #: 前置/输入条件描述
    preconditions: str = Field(default="")
    #: 操作步骤（LLM 自然语言，后续可编译为目标 DSL/pytest）
    steps: list[str] = Field(default_factory=list)
    #: 预期结果
    expected: str = Field(description="预期结果")
    #: 该用例覆盖的需求编号（对应 requirements 下标或 SR 号）
    covers: list[str] = Field(default_factory=list)
    #: 用例级别
    tier: Literal["smoke", "safety", "regression"] = "smoke"
    #: 机器可读执行意图（可选；缺失则仅自然语言，真实执行按 uncompiled 计）
    execution: Optional[ExecutionIntent] = None

    def is_valid(self) -> bool:
        """结构完整性的最小判定：名字/目的/预期不能为空。

        带 execution 的用例额外要求机器可执行（否则视为生成质量缺陷，
        下游真实执行会把它计为 uncompiled/失败，口径诚实）。
        """
        if not (self.name.strip() and self.purpose.strip() and self.expected.strip()):
            return False
        if self.execution is not None and not self.execution.is_executable():
            return False
        return True


class GenReport(BaseModel):
    """一批生成结果的量化报告。"""

    request: GenRequest
    #: 生成的用例（已通过结构校验）
    cases: list[GeneratedCase] = Field(default_factory=list)
    #: 生成失败的原始条目数（LLM 输出无法解析）
    failures: int = Field(default=0)
    #: LLM-as-judge 质量分（0-100），None 表示未做
    quality_score: Optional[float] = None
    #: 生成的用例在本仓库确定性执行器上跑出的通过率（0-1），None 表示未跑
    exec_pass_rate: Optional[float] = None
    #: 目标模块覆盖率的提升评估（0-1），None 表示未评估
    coverage_gain: Optional[float] = None

    @property
    def parse_rate(self) -> float:
        """结构可解析率 = 成功用例数 / (成功用例数 + 失败数)。"""
        total = len(self.cases) + self.failures
        if total == 0:
            return 0.0
        return len(self.cases) / total

    def summary(self) -> dict[str, object]:
        """压成可打印/可写 JSON 的关键指标。"""
        return {
            "target": self.request.target,
            "requested": self.request.num_cases,
            "parsed": len(self.cases),
            "failures": self.failures,
            "parse_rate": round(self.parse_rate, 3),
            "quality_score": self.quality_score,
            "exec_pass_rate": self.exec_pass_rate,
            "coverage_gain": self.coverage_gain,
        }
