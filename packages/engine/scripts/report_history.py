#!/usr/bin/env python3
"""测试趋势报表：聚合 reports/junit*.xml → Markdown / ASCII。

用法::

    python scripts/report_history.py            # 渲染 Markdown（stdout）
    python scripts/report_history.py --ascii    # 渲染 ASCII 趋势条
    python scripts/report_history.py -o TREND.md # 写文件
    python scripts/report_history.py --dir reports
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcms.reporting import collect_history, render_ascii, render_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="测试趋势报表（解析 JUnit 产物）")
    parser.add_argument("--dir", default="reports", help="报告目录（默认 reports/）")
    parser.add_argument("--pattern", default="junit*.xml", help="文件通配（默认 junit*.xml）")
    parser.add_argument("--ascii", action="store_true", help="输出 ASCII 趋势图而非 Markdown")
    parser.add_argument("-o", "--output", help="写文件（默认 stdout）")
    args = parser.parse_args()

    runs = collect_history(args.dir, args.pattern)
    text = render_ascii(runs) if args.ascii else render_markdown(runs)

    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.output}")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
