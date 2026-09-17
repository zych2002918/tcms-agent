"""核心链路测试：解析 / 执行 / 打分 / 流水线（全离线 mock）。"""

import pytest

from tcms_ai_testgen.executor import run_deterministic
from tcms_ai_testgen.judge import judge_quality
from tcms_ai_testgen.llm import OpenAICompatClient, extract_json, parse_cases
from tcms_ai_testgen.models import GeneratedCase, GenReport, GenRequest
from tcms_ai_testgen.pipeline import demo_default, run_pipeline


def _req() -> GenRequest:
    return GenRequest(
        target="EBM",
        requirements=["超速触发制动", "断线告警"],
        num_cases=6,
        tier="safety",
    )


class TestExtractJson:
    def test_fenced_json(self) -> None:
        text = '前缀\n```json\n{"cases": []}\n```\n后缀'
        assert extract_json(text) == {"cases": []}

    def test_plain_json(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_garbage(self) -> None:
        assert extract_json("完全没有 JSON") is None
        assert extract_json("") is None

    def test_truncated_tail_after_object(self) -> None:
        # 对象完整闭合但后面被截断/有杂讯：应能兜底解析
        text = '{"cases": [{"name": "x", "purpose": "p", "expected": "e"}]} 后置杂讯截断'
        assert extract_json(text) is not None

    def test_unclosed_inner_returns_none(self) -> None:
        # 大括号不闭合（对象内部截断）无法可靠恢复 -> None
        assert extract_json('{"cases": [{"name": "x"}') is None

    def test_non_string_input(self) -> None:
        assert extract_json(123) is None  # type: ignore[arg-type]


class TestParseCases:
    def test_non_dict_payload_fails_once(self) -> None:
        ok, fail = parse_cases("not a dict")
        assert ok == []
        assert fail == 1

    def test_valid_cases(self) -> None:
        ok, fail = parse_cases(
            {
                "cases": [
                    {
                        "name": "test_ebm_trigger",
                        "purpose": "超速触发制动",
                        "steps": ["加速"],
                        "expected": "触发紧急制动",
                        "covers": ["0"],
                        "tier": "safety",
                    }
                ]
            }
        )
        assert len(ok) == 1
        assert fail == 0

    def test_missing_expected_counts_as_failure(self) -> None:
        ok, fail = parse_cases({"cases": [{"name": "x", "purpose": "y"}]})
        assert len(ok) == 0
        assert fail == 1

    def test_accepts_items_alias(self) -> None:
        ok, fail = parse_cases({"items": [{"name": "a", "purpose": "p", "expected": "e"}]})
        assert len(ok) == 1
        assert fail == 0


class TestDeterministicExec:
    def _case(self, name: str, expected: str) -> GeneratedCase:
        return GeneratedCase(name=name, purpose="p", expected=expected)

    def test_good_cases_pass(self) -> None:
        cases = [self._case("test_ebm_trigger", "触发紧急制动")]
        r = run_deterministic(cases)
        assert r.total == 1
        assert r.passed == 1
        assert r.exec_pass_rate == 1.0

    def test_broken_name_not_compiled(self) -> None:
        cases = [self._case("broken_1", "触发紧急制动")]
        r = run_deterministic(cases)
        assert r.compiled == 0
        assert r.exec_pass_rate == 0.0

    def test_vague_expected_fails(self) -> None:
        cases = [self._case("test_vague", "结果应该差不多")]  # 无动作词/否定词
        r = run_deterministic(cases)
        assert r.passed == 0


class TestJudge:
    def test_quality_in_range(self) -> None:
        case = GeneratedCase(name="test_ebm_trigger", purpose="验证边界超速", expected="触发紧急制动")
        assert 0 <= judge_quality([case]) <= 100

    def test_empty_returns_zero(self) -> None:
        assert judge_quality([]) == 0.0


class TestPipeline:
    def test_mock_pipeline_returns_report(self) -> None:
        report = run_pipeline(_req())
        assert isinstance(report, GenReport)
        assert report.parse_rate > 0  # mock 会故意产生 failure，但大部分应解析成功
        assert report.quality_score is not None
        assert report.exec_pass_rate is not None

    def test_demo_default_runs(self) -> None:
        report = demo_default()
        assert report.request.target == "列车 TCMS 紧急制动管理（EBM）"
        assert len(report.cases) > 0


class TestOpenAICompatClient:
    def test_requires_llm_extra_when_openai_missing(self, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "openai":
                raise ImportError("no openai")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(RuntimeError, match="llm"):
            OpenAICompatClient()

