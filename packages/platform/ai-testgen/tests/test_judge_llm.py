"""judge_llm 测试：解析/汇总逻辑离线；真实 LLM 交叉验证为实验（examples）。"""

from __future__ import annotations

from tcms_ai_testgen.judge_llm import (
    CaseJudge,
    LlmJudgeResult,
    _parse_scores,
    judge_cases_with_llm,
)
from tcms_ai_testgen.models import GeneratedCase


class _FakeClient:
    def __init__(self, response: str):
        self._resp = response
        self.last_prompt = ""

    def complete(self, prompt: str, temperature=None) -> str:
        self.last_prompt = prompt
        return self._resp


def _case(name: str, expected: str = "触发紧急制动并报警", execution=None) -> GeneratedCase:
    return GeneratedCase(name=name, purpose="验证边界超速", expected=expected,
                         execution=execution)


class TestParseScores:
    def test_parses_valid(self) -> None:
        out = '''```json
{"scores": [{"name": "test_a", "score": 88, "reason": "边界清晰"}]}
```'''
        r = _parse_scores(out)
        assert r == {"test_a": (88.0, "边界清晰")}

    def test_garbage(self) -> None:
        assert _parse_scores("不是 JSON") == {}
        assert _parse_scores('{"nope": 1}') == {}

    def test_partial_skip(self) -> None:
        out = '{"scores": [{"name": "a", "score": 90, "reason": "x"}, {"name": 123, "score": "bad"}]}'
        r = _parse_scores(out)
        assert "a" in r and 90.0 in [r["a"][0]]


class TestResultStats:
    def test_mean_diff_and_agreement(self) -> None:
        r = LlmJudgeResult(cases=[
            CaseJudge(name="a", rule_score=80, llm_score=85, llm_reason=""),
            CaseJudge(name="b", rule_score=90, llm_score=50, llm_reason="语义错配"),
        ])
        assert r.mean_abs_diff == 22.5  # (5+40)/2
        assert r.agreement_rate == 0.5  # 一条 diff<=15

    def test_empty(self) -> None:
        r = LlmJudgeResult()
        assert r.mean_abs_diff == 0.0
        assert r.agreement_rate == 0.0

    def test_as_dict(self) -> None:
        r = LlmJudgeResult(cases=[CaseJudge(name="a", rule_score=80, llm_score=85)])
        d = r.as_dict()
        assert d["cases"][0]["diff"] == 5.0


class TestJudgeWithLlm:
    def test_matches_by_name(self) -> None:
        cases = [
            _case("test_boundary_ok"),
            _case("test_vague_one", expected="结果应该差不多"),
        ]
        resp = '''{"scores": [
            {"name": "test_boundary_ok", "score": 90, "reason": "边界明确"},
            {"name": "test_vague_one", "score": 30, "reason": "表述模糊"}]}'''
        res = judge_cases_with_llm(cases, _FakeClient(resp))
        assert len(res.cases) == 2
        by_name = {c.name: c for c in res.cases}
        assert by_name["test_boundary_ok"].llm_score == 90.0
        assert by_name["test_vague_one"].llm_score == 30.0

    def test_missing_scores_fallback_to_rule(self) -> None:
        cases = [_case("test_a")]
        res = judge_cases_with_llm(cases, _FakeClient("{}"))
        assert res.cases[0].llm_score == res.cases[0].rule_score  # 回退规则分

    def test_client_requires_complete(self) -> None:
        import pytest

        class NoComplete:
            pass

        with pytest.raises(RuntimeError, match="complete"):
            judge_cases_with_llm([_case("test_a")], NoComplete())
