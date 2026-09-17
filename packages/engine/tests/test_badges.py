"""scripts/gen_badges.py 验证：README 徽章自证链路（审计 P1-3 根治）。

徽章数字必须来自机器产物（JUnit + coverage.json），禁止手抄漂移。
本测试验证解析/渲染/就地改写三段逻辑；README 的 badges 标记完整性
另由 CI 的 gen_badges --check 保障。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import gen_badges

REPO = Path(__file__).resolve().parent.parent


def _write_junit(tmp_path: Path, tests=707, skipped=1, failures=0, errors=0) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(
        f'<testsuite name="pytest" tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}" time="1.0">'
        "<testcase classname='t' name='x'/></testsuite>",
        encoding="utf-8",
    )
    return p


def _write_coverage(tmp_path: Path, pct=97.81) -> Path:
    p = tmp_path / "coverage.json"
    p.write_text(json.dumps({"meta": {}, "totals": {"percent_covered": pct}}), encoding="utf-8")
    return p


def test_parse_junit(tmp_path):
    stats = gen_badges.parse_junit(_write_junit(tmp_path))
    assert stats == {"tests": 707, "failures": 0, "errors": 0, "skipped": 1}


def test_parse_junit_nested_testsuites(tmp_path):
    """pytest --junitxml 产物是 <testsuites> 根 + <testsuite> 子元素。"""
    p = tmp_path / "junit.xml"
    p.write_text(
        '<testsuites name="pytest"><testsuite name="pytest" tests="724" '
        'failures="1" errors="0" skipped="1"><testcase name="x"/></testsuite>'
        "</testsuites>",
        encoding="utf-8",
    )
    assert gen_badges.parse_junit(p) == {
        "tests": 724,
        "failures": 1,
        "errors": 0,
        "skipped": 1,
    }


def test_parse_junit_rejects_unknown_root(tmp_path):
    p = tmp_path / "junit.xml"
    p.write_text("<not-junit/>", encoding="utf-8")
    with pytest.raises(ValueError):
        gen_badges.parse_junit(p)


def test_parse_coverage_json(tmp_path):
    assert gen_badges.parse_coverage_json(_write_coverage(tmp_path)) == 97.81


def test_parse_coverage_json_missing_totals(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        gen_badges.parse_coverage_json(p)


def test_build_badges_contains_derived_numbers():
    badges = gen_badges.build_badges(tests=707, skipped=1, coverage=97.81)
    # 通过数 = tests - skipped = 706
    assert "tests-706%20passed" in badges
    assert "tests=707 (skipped 1, failures 0, errors 0) coverage=97.81%" in badges
    # 覆盖率整数化
    assert "coverage-98%25" in badges


def test_build_badges_failure_turns_red():
    """有失败时徽章必须显红（防失败态亮绿误导）。"""
    badges = gen_badges.build_badges(tests=707, skipped=1, coverage=97.81, failures=3)
    assert "-red)" in badges
    assert "3%20failed" in badges
    assert "tests=707 (skipped 1, failures 3, errors 0)" in badges


def test_patch_readme_roundtrip(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "before\n"
        f"{gen_badges.BADGES_START}\n"
        "[![CI](x)](y)\n"  # 非测试徽章：保留
        "[![tests: 99](x)](#)\n"  # 测试徽章：替换
        "[![coverage: 42%](x)](#)\n"
        f"{gen_badges.BADGES_END}\nafter\n",
        encoding="utf-8",
    )
    gen_badges.patch_readme(readme, gen_badges.build_badges(tests=707, skipped=1, coverage=97.81))
    text = readme.read_text(encoding="utf-8")
    assert "[![CI](x)](y)" in text  # 保留
    assert "[![tests: 99](x)](#)" not in text  # 替换
    assert "[![coverage: 42%](x)](#)" not in text
    assert "tests-706%20passed" in text
    assert text.startswith("before\n") and text.endswith("after\n")


def test_patch_readme_requires_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("no markers here", encoding="utf-8")
    with pytest.raises(ValueError):
        gen_badges.patch_readme(readme, "[![x](#)")
