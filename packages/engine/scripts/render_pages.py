#!/usr/bin/env python3
"""渲染 GitHub Pages 实时测试报告 —— 消除 bash heredoc 内联脚本。

被 .github/workflows/pages.yml（workflow_run 模式）调用：CI 成功后
download-artifact 拿到 3.12 的 JUnit / coverage.json / pytest HTML，
本脚本把它们渲染成 docs/reports/ 下的站点产物：

    docs/reports/TREND.md      # JUnit 历史趋势（Markdown）
    docs/reports/TREND.txt     # JUnit 历史趋势（ASCII）
    docs/reports/report.html   # 最新 pytest HTML 报告
    docs/reports/latest.json   # 站点 hero 动态统计（index.html fetch）

用法::

    python scripts/render_pages.py --artifacts artifacts/ --out docs/reports/

--run-id 可选（写入 latest.json，便于追溯本次 CI run）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcms.reporting import collect_history, render_ascii, render_markdown  # noqa: E402


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


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 Pages 实时测试报告")
    parser.add_argument("--artifacts", required=True, help="CI artifact 目录")
    parser.add_argument("--out", required=True, help="输出目录（如 docs/reports/）")
    parser.add_argument("--junit-name", default="junit-py3.12.xml", help="JUnit 文件名")
    parser.add_argument("--coverage-name", default="coverage.json", help="coverage JSON 文件名")
    parser.add_argument("--html-name", default="report.html", help="pytest HTML 文件名")
    parser.add_argument("--run-id", default="", help="CI run id（写入 latest.json）")
    args = parser.parse_args()

    art = Path(args.artifacts)
    out = Path(args.out)
    # 打印实际布局，便于 CI 失败时定位（download-artifact 可能带子目录）
    found = sorted(str(p.relative_to(art)) for p in art.rglob("*") if p.is_file())
    print(f"artifacts 内容: {found}")

    # 容忍 artifact 解包到子目录（upload 用 path: report.html 等根文件）
    def _resolve(name: str) -> Path:
        direct = art / name
        if direct.exists():
            return direct
        hits = sorted(art.rglob(name))
        if not hits:
            raise FileNotFoundError(f"{art} 下找不到 {name}（实际: {found}）")
        return hits[0]

    junit = _resolve(args.junit_name)
    stats = parse_junit(junit)

    out.mkdir(parents=True, exist_ok=True)
    # 3.12 全量 JUnit 作为最新历史点（多版本历史由不同文件名聚合）
    master = art / "junit-master.xml"
    shutil.copyfile(junit, master)
    runs = collect_history(str(art), "junit-master.xml")
    (out / "TREND.md").write_text(render_markdown(runs) + "\n", encoding="utf-8")
    (out / "TREND.txt").write_text(render_ascii(runs) + "\n", encoding="utf-8")

    html = _resolve(args.html_name)
    if html.exists():
        shutil.copyfile(html, out / "report.html")

    coverage = json.loads(_resolve(args.coverage_name).read_text(encoding="utf-8"))
    data = {
        "tests": stats["tests"],
        "skipped": stats["skipped"],
        "failures": stats["failures"],
        "errors": stats["errors"],
        "coverage": round(coverage["totals"]["percent_covered"], 2),
        "python": "3.12",
        "run_id": args.run_id,
        "run_count": len(runs),
    }
    (out / "latest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已渲染 {out}: tests={data['tests']} coverage={data['coverage']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
