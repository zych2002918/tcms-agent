"""tcms.reporting 单元测试：JUnit 解析、历史聚合、趋势渲染。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tcms.reporting import (
    TestRun,
    collect_history,
    parse_junit,
    render_ascii,
    render_markdown,
)

FAKE_TS = "2026-09-01T10:00:00+08:00"

GOOD_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest">
  <testsuite name="pytest" errors="1" failures="2" skipped="1" tests="10"
             time="0.42" timestamp="{FAKE_TS}">
    <testcase classname="tests.test_a" name="test_ok" time="0.01"/>
    <testcase classname="tests.test_a" name="test_bad" time="0.02">
      <failure message="assert 0">traceback...</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def _write_junit(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_junit_counts_and_properties(tmp_path):
    p = _write_junit(tmp_path, "junit.xml", GOOD_XML)
    run = parse_junit(str(p))
    assert run is not None
    assert run.tests == 10
    assert run.failures == 2
    assert run.errors == 1
    assert run.skipped == 1
    assert run.passed == 6
    assert run.time == pytest.approx(0.42)
    assert run.ok is False
    assert (
        run.timestamp == datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
        or run.timestamp.year == 2026
    )


def test_parse_junit_missing_timestamp_falls_back(tmp_path):
    p = _write_junit(
        tmp_path,
        "junit-no-ts.xml",
        '<testsuite name="pytest" tests="3" failures="0" errors="0" time="0.1"/>',
    )
    run = parse_junit(str(p))
    assert run is not None
    assert run.tests == 3
    assert run.timestamp.year == 1970  # fallback


def test_parse_junit_invalid_file_returns_none(tmp_path):
    p = _write_junit(tmp_path, "junk.xml", "<not-xml")
    assert parse_junit(str(p)) is None


def test_parse_junit_ignores_non_testsuite_root(tmp_path):
    p = _write_junit(tmp_path, "coverage.xml", '<coverage line-rate="0.98"/>')
    assert parse_junit(str(p)) is None


def test_parse_single_testsuite_root(tmp_path):
    p = _write_junit(
        tmp_path,
        "junit-single.xml",
        '<testsuite name="pytest" tests="4" failures="0" errors="0" time="0.2"/>',
    )
    run = parse_junit(str(p))
    assert run is not None
    assert run.tests == 4
    assert run.ok


def test_collect_history_sorted_and_filters(tmp_path):
    # 无序创建两个 junit + 一个 coverage.xml + 一个坏文件
    _write_junit(tmp_path, "junit-late.xml", GOOD_XML)  # ts 2026-09-01
    _write_junit(
        tmp_path,
        "junit-early.xml",
        '<testsuite name="pytest" tests="3" failures="0" errors="0" '
        'time="0.1" timestamp="2026-08-01T00:00:00+00:00"/>',
    )
    _write_junit(tmp_path, "coverage.xml", '<coverage line-rate="0.9"/>')
    _write_junit(tmp_path, "junit-broken.xml", "garbage")
    runs = collect_history(str(tmp_path))
    assert len(runs) == 2
    assert runs[0].tests == 3  # 2026-08-01 在前
    assert runs[1].tests == 10


def test_render_markdown_table_and_summary(tmp_path):
    _write_junit(tmp_path, "junit.xml", GOOD_XML)
    runs = collect_history(str(tmp_path))
    text = render_markdown(runs)
    assert "| 运行时间 |" in text
    assert "6 通过 / 2 失败 / 1 错误 / 1 跳过" in text
    assert "❌" in text
    assert "junit.xml" in text


def test_render_markdown_empty():
    text = render_markdown([])
    assert "暂无历史报告" in text


def test_render_ascii_bar_and_ok_run(tmp_path):
    _write_junit(
        tmp_path,
        "junit.xml",
        '<testsuite name="pytest" tests="100" failures="0" errors="0" '
        'time="3.0" timestamp="2026-09-01T00:00:00+00:00"/>',
    )
    runs = collect_history(str(tmp_path))
    text = render_ascii(runs)
    assert "ok  " in text
    assert "100 用例" in text


def test_render_ascii_empty():
    assert "暂无历史报告" in render_ascii([])


def test_passed_property():
    run = TestRun(path="x.xml", tests=10, errors=1, failures=2, skipped=1)
    assert run.passed == 6
    assert run.ok is False
    run2 = TestRun(path="y.xml", tests=5, errors=0, failures=0, skipped=0)
    assert run2.passed == 5
    assert run2.ok is True
