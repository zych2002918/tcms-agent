#!/usr/bin/env python3
"""README 测试徽章自证生成器 —— 消除"手抄数字"漂移。

审计 P1-3：徽章数字（tests/coverage）此前手写于 README，改测试后
极易与真实结果漂移。本脚本从**机器可读产物**读取真实数字：
    - `--junit`: pytest --junitxml 产物（tests / skipped）
    - `--coverage-json`: coverage.py 的 `--cov-report=json`（coverage.json，含
      `totals.percent_covered` 语句覆盖率，与 --cov-report=term 口径一致）

只重写 README 中 `<!-- badges:start -->` / `<!-- badges:end -->` 之间
的徽章段（其余内容不触碰），保证可重复执行（幂等）。

用法::

    pytest tests/ --junitxml=reports/junit.xml
    coverage json -o coverage.json            # 或 pytest --cov-report=json
    python scripts/gen_badges.py --junit reports/junit.xml --coverage-json coverage.json
    python scripts/gen_badges.py --junit reports/junit.xml --coverage-json coverage.json --readme README.md
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

BADGES_START = "<!-- badges:start -->"
BADGES_END = "<!-- badges:end -->"


def parse_junit(path: str | Path) -> dict:
    """从 JUnit XML 读取 {tests, failures, errors, skipped}。

    pytest --junitxml 根为 <testsuites>，统计属性在首个 <testsuite> 子元素；
    兼容直接以 <testsuite> 为根的产物。
    """
    root = ET.parse(path).getroot()
    if root.tag == "testsuites":
        suite = next(iter(root))
        if suite.tag != "testsuite":
            raise ValueError(f"{path}: testsuites 下缺少 testsuite 元素")
    elif root.tag == "testsuite":
        suite = root
    else:
        raise ValueError(f"{path}: 不是 JUnit XML（根元素 {root.tag!r}）")
    return {
        "tests": int(suite.attrib.get("tests", 0)),
        "failures": int(suite.attrib.get("failures", 0)),
        "errors": int(suite.attrib.get("errors", 0)),
        "skipped": int(suite.attrib.get("skipped", 0)),
    }


def parse_coverage_json(path: str | Path) -> float:
    """从 coverage.py JSON 读取语句覆盖率百分比。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    pct = totals.get("percent_covered")
    if pct is None:
        raise ValueError(f"{path}: 缺少 totals.percent_covered")
    return float(pct)


def build_badges(
    tests: int, skipped: int, coverage: float, failures: int = 0, errors: int = 0
) -> str:
    """渲染 markdown 徽章段（shield.io 静态徽章 + 自证注解行）。

    只含 tests / coverage 两枚（调用方 patch_readme 保留区内其他徽章）。
    failures/errors 非零时渲染红色失败徽章（防失败态亮绿）。
    """
    bad = failures + errors
    passed = tests - skipped - bad  # JUnit tests 含 skipped + failures
    color = "red" if bad else "brightgreen"
    suffix = f"-{bad}%20failed" if bad else ""
    passed_badge = (
        f"[![tests: {passed}](https://img.shields.io/badge/tests-{passed}%20passed"
        f"{suffix}-{color})](#)"
    )
    cov_int = round(coverage)
    coverage_badge = (
        f"[![coverage: {cov_int}%](https://img.shields.io/badge/"
        f"coverage-{cov_int}%25-brightgreen)](#)"
    )
    return "\n".join(
        [
            f"{passed_badge} {coverage_badge}",
            f"<!-- 自证：tests={tests} (skipped {skipped}, failures {failures}, "
            f"errors {errors}) coverage={coverage:.2f}% "
            f"— 由 scripts/gen_badges.py 依据 JUnit + coverage.json 生成 -->",
        ]
    )


def patch_readme(readme_path: str | Path, badges: str) -> None:
    """把 README 中 badges:start/end 之间的内容替换为生成的徽章段。

    保留区内原有的非 tests/coverage 徽章行（CI / Python / License / Safety…），
    只让 tests/coverage 两枚由机器产物自证——其余徽章是仓库元数据，非测试结果。
    """
    p = Path(readme_path)
    text = p.read_text(encoding="utf-8")
    if BADGES_START not in text or BADGES_END not in text:
        raise ValueError(f"{p}: 缺少 {BADGES_START!r} / {BADGES_END!r} 标记，拒绝改写 README")
    before, _sep, after = text.partition(BADGES_START)
    inner, _sep2, after = after.partition(BADGES_END)
    # 取出区内原有行，保留与测试结果无关的徽章行
    kept = [
        ln
        for ln in inner.splitlines()
        if ln.strip()
        and not any(m in ln for m in ("tests-", "tests:", "coverage-", "coverage:"))
        and not ln.strip().startswith("<!--")
    ]
    block = "\n".join([*kept, badges]).rstrip()
    new_text = f"{before}{BADGES_START}\n{block}\n{BADGES_END}{after}"
    p.write_text(new_text, encoding="utf-8")
    print(f"已更新 {p} 徽章段")


def main() -> int:
    parser = argparse.ArgumentParser(description="README 徽章自证更新")
    parser.add_argument("--junit", required=True, help="pytest JUnit XML 路径")
    parser.add_argument("--coverage-json", required=True, help="coverage.py JSON 路径")
    parser.add_argument(
        "--readme",
        default="README.md",
        help="要更新的 README（默认 README.md）",
    )
    args = parser.parse_args()

    stats = parse_junit(args.junit)
    coverage = parse_coverage_json(args.coverage_json)
    badges = build_badges(
        stats["tests"],
        stats["skipped"],
        coverage,
        failures=stats["failures"],
        errors=stats["errors"],
    )
    print(badges)
    patch_readme(args.readme, badges)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
