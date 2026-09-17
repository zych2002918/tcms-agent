"""TCMS-CAN-Test 仓库态一键入口（薄壳）→ tcms.cli（包内实现）。

用法:
    python run.py                     # 全量测试 + HTML 报告（默认）
    python run.py --level smoke       # 冒烟层（核心安全路径，<1s）
    python run.py -k door             # 按用例名关键字筛选（透传 pytest -k）
    python run.py --junitxml          # 额外输出 JUnit XML（CI/工具链）
    python run.py --allure            # 同时生成 Allure 结果（allure-results/）
    python run.py --replay demo.asc   # 用完整回放链驱动 .asc 日志并出报告
    python run.py --doctor            # 环境自检

真实 CLI 逻辑在 `tcms/cli.py`（wheel 分发后由 `tcms-test` 入口复用同实现，
保证仓库态与安装态行为一致——单一真源）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tcms.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
