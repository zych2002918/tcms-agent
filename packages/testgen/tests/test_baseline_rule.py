"""规则基线生成源测试：机械边界推导、DSL 合法性、可编译性。"""

from __future__ import annotations

from tcms_ai_testgen.baseline_rule import RuleBaselineClient
from tcms_ai_testgen.executor_real import compile_case
from tcms_ai_testgen.llm import extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest


def _generated() -> list:
    raw = RuleBaselineClient().generate_cases(GenRequest(target="TCMS", num_cases=5))
    cases, fails = parse_cases(extract_json(raw))
    return cases, fails


class TestRuleBaseline:
    def test_generates_ten_bound_cases(self) -> None:
        cases, fails = _generated()
        assert fails == 0
        assert len(cases) == 10  # 5 信号 × (越界 + 上限)
        assert all(c.execution is not None for c in cases)
        assert all(c.execution.kind == "encode_bound" for c in cases)

    def test_all_compilable(self) -> None:
        cases, _ = _generated()
        compiled = [c for c in cases if compile_case(c)]
        assert len(compiled) == len(cases)  # 规则模板 DSL 全合法

    def test_names_unique(self) -> None:
        cases, _ = _generated()
        names = [c.name for c in cases]
        assert len(set(names)) == len(names)

    def test_pairs_reject_and_ok(self) -> None:
        cases, _ = _generated()
        ops = [e.op for c in cases for e in c.execution.expect]
        assert "expect_encode_error" in ops
        assert "expect_encode_ok" in ops

    def test_interface_matches_llm_client(self) -> None:
        """同 generate_cases(req)->str 契约（与 MockLLM 互换）。"""
        client = RuleBaselineClient()
        raw = client.generate_cases(GenRequest(target="x", num_cases=3))
        assert isinstance(raw, str)
        assert '"cases"' in raw
