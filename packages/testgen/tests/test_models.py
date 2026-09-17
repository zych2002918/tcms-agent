"""模型约束与边界测试。"""

import pytest
from pydantic import ValidationError

from tcms_ai_testgen.models import GeneratedCase, GenReport, GenRequest


class TestGenRequest:
    def test_defaults(self) -> None:
        req = GenRequest(target="X")
        assert req.num_cases == 5
        assert req.tier == "smoke"
        assert req.requirements == []

    def test_num_cases_range(self) -> None:
        with pytest.raises(ValidationError):
            GenRequest(target="X", num_cases=0)
        with pytest.raises(ValidationError):
            GenRequest(target="X", num_cases=51)

    def test_bad_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GenRequest(target="X", tier="bogus")  # type: ignore[arg-type]


class TestGeneratedCase:
    def test_is_valid_min(self) -> None:
        c = GeneratedCase(name="test_a", purpose="p", expected="e")
        assert c.is_valid()

    def test_is_valid_incomplete(self) -> None:
        c = GeneratedCase(name="  ", purpose="p", expected="e")
        assert not c.is_valid()
        c2 = GeneratedCase(name="test_a", purpose="", expected="e")
        assert not c2.is_valid()


class TestGenReport:
    def test_parse_rate(self) -> None:
        r = GenReport(request=GenRequest(target="X"), cases=[], failures=4)
        assert r.parse_rate == 0.0
        case = GeneratedCase(name="test_a", purpose="p", expected="e")
        r2 = GenReport(request=GenRequest(target="X"), cases=[case], failures=0)
        assert r2.parse_rate == 1.0

    def test_summary_keys(self) -> None:
        r = GenReport(request=GenRequest(target="X"))
        s = r.summary()
        for k in ("target", "requested", "parsed", "parse_rate", "quality_score"):
            assert k in s
