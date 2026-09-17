"""测试报告聚合与趋势分析（stdlib only）。

把 CI / run.py 落盘的 JUnit XML（reports/junit.xml、reports/junit-*.xml）
解析为结构化记录，并渲染跨版本回归趋势（Markdown / ASCII）。

设计约定：
- 任何单文件解析失败只跳过该文件，不影响其余文件（best effort）。
- 不依赖 pytest / pytest-xml 运行时对象，只解析 XML 文本，因此可对
  历史产物离线分析。

用法（由 scripts/report_history.py 薄壳包装）::

    from tcms.reporting import collect_history, render_markdown

    runs = collect_history("reports")
    print(render_markdown(runs))
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

# 时间戳容差：早期报告可能没有 timestamp 属性。
_FALLBACK_TS = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class TestRun:
    """一次测试运行（对应一个 JUnit testsuite/testsuites 文件）。"""

    __test__ = False  # 防止被 pytest 当作测试类收集

    path: str
    tests: int = 0
    errors: int = 0
    failures: int = 0
    skipped: int = 0
    time: float = 0.0
    timestamp: datetime = _FALLBACK_TS
    suites: list[dict] = field(default_factory=list)  # 每 suite: name/classname/tests/failures

    @property
    def passed(self) -> int:
        return self.tests - self.errors - self.failures - self.skipped

    @property
    def ok(self) -> bool:
        return self.errors == 0 and self.failures == 0


def _parse_ts(value: str | None) -> datetime:
    """解析 ISO 时间戳；缺省/非法回退到 _FALLBACK_TS。"""
    if not value:
        return _FALLBACK_TS
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _FALLBACK_TS


def parse_junit(path: str) -> TestRun | None:
    """解析单个 JUnit XML 文件为 TestRun；解析失败返回 None（不抛）。"""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    run = TestRun(path=os.path.basename(path))
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        return None
    first_ts: datetime | None = None
    for suite in suites:
        attrs = suite.attrib
        suite_tests = int(attrs.get("tests", 0) or 0)
        suite_fail = int(attrs.get("failures", 0) or 0)
        suite_err = int(attrs.get("errors", 0) or 0)
        run.tests += suite_tests
        run.failures += suite_fail
        run.errors += suite_err
        run.time += float(attrs.get("time", 0.0) or 0.0)
        ts = _parse_ts(attrs.get("timestamp"))
        if first_ts is None or ts < first_ts:
            first_ts = ts
        run.suites.append(
            {
                "name": attrs.get("name", ""),
                "classname": attrs.get("classname", suite.get("name", "")),
                "tests": suite_tests,
                "failures": suite_fail,
                "errors": suite_err,
                "skipped": int(attrs.get("skipped", 0) or 0),
            }
        )
    run.skipped = sum(s.get("skipped", 0) for s in run.suites)
    run.timestamp = first_ts or _FALLBACK_TS
    return run


def collect_history(reports_dir: str, pattern: str = "junit*.xml") -> list[TestRun]:
    """扫描目录下所有 JUnit 产物，按时间戳升序返回 TestRun 列表。

    只收集根元素为 testsuite(s) 的文件——coverage.xml 等其它 XML
    天然被排除（根元素不匹配时 parse 返回 None）。
    """
    runs: list[TestRun] = []
    for path in sorted(glob.glob(os.path.join(reports_dir, pattern))):
        run = parse_junit(path)
        if run is not None:
            runs.append(run)
    runs.sort(key=lambda r: r.timestamp)
    return runs


def _ts_fmt(ts: datetime) -> str:
    if ts == _FALLBACK_TS:
        return "-"
    return ts.astimezone().strftime("%Y-%m-%d %H:%M")


def render_markdown(runs: list[TestRun]) -> str:
    """渲染为 Markdown 趋势表（供 README/Pages 引用）。"""
    if not runs:
        return "（暂无历史报告：先在 reports/ 落盘 junit XML 再运行本脚本）"
    lines = [
        "| 运行时间 | 来源文件 | 总数 | 通过 | 失败 | 错误 | 跳过 | 耗时(s) | 结果 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {:.1f} | {} |".format(
                _ts_fmt(r.timestamp),
                f"`{r.path}`",
                r.tests,
                r.passed,
                r.failures,
                r.errors,
                r.skipped,
                r.time,
                "✅" if r.ok else "❌",
            )
        )
    total = runs[-1]
    lines.append("")
    lines.append(
        f"最近一次（{_ts_fmt(total.timestamp)}）：{total.tests} 用例，"
        f"{total.passed} 通过 / {total.failures} 失败 / {total.errors} 错误 / "
        f"{total.skipped} 跳过，结果{'✅ 通过' if total.ok else '❌ 失败'}。"
    )
    return "\n".join(lines)


def render_ascii(runs: list[TestRun], width: int = 20) -> str:
    """渲染轻量 ASCII 趋势图（失败率列）与最近一次摘要。"""
    if not runs:
        return "（暂无历史报告）"
    out = ["跨运行失败率趋势（每行一次运行）：", ""]
    for r in runs:
        failed = r.failures + r.errors
        rate = failed / r.tests if r.tests else 0.0
        filled = int(round(rate * width))
        bar = "#" * filled + "." * (width - filled)
        flag = "FAIL" if r.failures or r.errors else "ok  "
        out.append(f"  {_ts_fmt(r.timestamp)}  {flag}  {bar}  {failed}/{r.tests}")
    last = runs[-1]
    out.append("")
    out.append(
        f"最新: {last.path}  {last.tests} 用例 / {last.passed} 通过 / "
        f"{last.failures} 失败 / {last.errors} 错误 / {last.skipped} 跳过"
    )
    return "\n".join(out)


def load_reporting_defaults() -> str:
    """返回报告目录约定说明（供 CLI --help 使用）。"""
    return "reports/"
