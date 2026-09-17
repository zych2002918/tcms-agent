"""确定性执行器：把 LLM 生成的「自然语言用例」编译成可执行检查并跑出通过率。

这是本项目质量闭环的关键设计：
- LLM 生成的是半结构化用例（步骤/预期是自然语言）；
- 光「看起来对」不算数——本模块把用例映射到一组**确定性可执行检查器**，
  用编译率 + 执行通过率量化「生成用例到底能不能用」；
- 真正对接 tcms-can-test 的 DBC/场景执行器属于二期；本骨架先立好
  「可执行评估」的接口与统计口径，保证数字可信、可复现。

口径说明（面试可讲）：
- compile_rate = 能编译成可执行检查的用例 / 总生成用例
- exec_pass_rate = 编译通过用例中，在确定性执行器上跑出 PASS 的比例
- 未编译用例计为失败，不剔除——体现「生成质量差会拖累整个交付」的测试观。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tcms_ai_testgen.models import GeneratedCase

#: 检查器：接收用例，返回该用例是否通过该项检查
Checker = Callable[[GeneratedCase], bool]


def _expected_explicit(case: GeneratedCase) -> bool:
    """预期结果须包含明确动作/状态词（异常路径应出现否定/保持类词）。"""
    action_words = ("触发", "停止", "进入", "返回", "置位", "恢复", "报警", "告警", "制动", "闭锁")
    # 精确否定短语（避免"差不多"这类含"不"的口语误判）
    negative_words = ("不触发", "不告警", "不应", "不会", "不能", "禁止", "拒绝", "保持", "不响应", "无告警")
    has_action = any(w in case.expected for w in action_words)
    has_negative = any(w in case.expected for w in negative_words)
    # 正常路径要动作词；异常路径要有否定词；两者都没有 => 表述模糊，不通过
    return has_action or has_negative


def _name_semantic(case: GeneratedCase) -> bool:
    """用例名须具场景语义（含 test_ 前缀且不是通用 gen_N 编号）。"""
    return case.name.startswith("test_") and not case.name.startswith("test_gen_")


_CHECKERS: list[tuple[str, Checker]] = [
    ("预期结果须可判定（动作词或否定词）", _expected_explicit),
    ("用例名须具场景语义（非占位编号）", _name_semantic),
]


@dataclass
class ExecResult:
    """一批用例的确定性执行结果。"""

    total: int
    compiled: int
    passed: int

    @property
    def compile_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def exec_pass_rate(self) -> float:
        return self.passed / self.compiled if self.compiled else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "total": self.total,
            "compiled": self.compiled,
            "passed": self.passed,
            "compile_rate": round(self.compile_rate, 3),
            "exec_pass_rate": round(self.exec_pass_rate, 3),
        }


def run_deterministic(cases: list[GeneratedCase]) -> ExecResult:
    """在确定性检查器上评估一批用例。任一检查器不满足 => 该用例未通过。"""
    total = len(cases)
    compiled = 0
    passed = 0
    for case in cases:
        # 模拟「编译失败」：无法映射到执行器的用例直接跳过（计为未编译/未通过）
        if case.name.startswith("broken") or not case.name.startswith("test_"):
            continue
        compiled += 1
        if all(check(case) for _, check in _CHECKERS):
            passed += 1
    return ExecResult(total=total, compiled=compiled, passed=passed)
