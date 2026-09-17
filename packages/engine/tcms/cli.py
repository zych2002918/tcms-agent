"""TCMS-CAN-Test 命令行入口（包内实现，供 console script / run.py 复用）。

pyproject 的 ``[project.scripts] tcms-test = "tcms.cli:main"`` 指向本模块——
wheel 安装后 ``tcms-test`` 命令开箱可用（此前入口指向仓库根 run.py，
分发后必然 ModuleNotFoundError，已修正）。

用法::

    tcms-test                     # 全量测试 + HTML 报告（仓库态：tests/）
    tcms-test --level smoke       # 冒烟层（核心安全路径，<1s）
    tcms-test -k door             # 按用例名关键字筛选
    tcms-test --junitxml          # 额外输出 JUnit XML（reports/junit.xml）
    tcms-test --allure            # 同时生成 Allure 结果（allure-results/）
    tcms-test --replay demo.asc   # 用完整回放链驱动 .asc 日志并出报告
    tcms-test --doctor            # 环境自检（依赖/版本/数据资产/总线/HIL）
    tcms-test --version
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ._version import __version__  # 直接引用版本单源模块，避免 __init__ 循环导入


def _version() -> str:
    """包版本（tcms/_version.py 单源）。"""
    return __version__


def _repo_tests_dir() -> Path | None:
    """仓库根 tests/（若存在）。pip 安装用户在任意目录运行时不强制。"""
    p = Path(__file__).resolve().parent.parent / "tests"
    return p if p.is_dir() else None


def replay_log(path: str) -> int:
    """回放 .asc 日志：ReplayChain 完整链（联锁/ATP/看门狗/EBM → 告警断言）。"""
    from . import replay

    chain = replay.ReplayChain.from_asc(path)
    report = chain.run()
    print(f"[replay] {path}: {report['frames']} 帧回放完成")
    print(
        f"[replay] EBM 状态: {report['ebm_state']}"
        f"{'（已触发紧急制动）' if report['ebm_triggered'] else ''}"
    )
    print(f"[replay] 看门狗: {report['watchdog_states']}")
    if report["alerts"]:
        print(f"[replay] 告警 {len(report['alerts'])} 条:")
        for a in report["alerts"]:
            print(f"  t={a['ts']:.3f}s {a['kind']}: {a['detail']}")
    else:
        print("[replay] 无告警（日志期间未触发任何安全事件）")
    print(f"[replay] 告警类别: {report['alert_kinds']}")
    return 0


def doctor() -> int:
    """环境自检（--doctor）：依赖/版本/数据资产/虚拟总线/HIL 状态。"""
    from . import diagnose

    rows = diagnose.run()
    diagnose.render(rows)
    return 0 if diagnose.all_ok(rows) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tcms-test",
        description="TCMS CAN 自动化测试入口（tcms-can-test）",
    )
    parser.add_argument("--version", action="version", version=f"tcms-can-test {_version()}")
    parser.add_argument(
        "--doctor", action="store_true", help="环境自检：依赖/版本/数据资产/总线/HIL 状态"
    )
    parser.add_argument(
        "--level",
        choices=("smoke", "full"),
        default="full",
        help="测试层级：smoke=冒烟层（核心安全路径）；full=全量回归（默认）",
    )
    parser.add_argument("--allure", action="store_true", help="生成 Allure 结果目录")
    parser.add_argument("--no-report", action="store_true", help="不生成 HTML 报告")
    parser.add_argument("--coverage", action="store_true", help="生成代码覆盖率报告（htmlcov/）")
    parser.add_argument(
        "--junitxml", action="store_true", help="额外输出 JUnit XML（reports/junit.xml）"
    )
    parser.add_argument("-k", dest="keyword", default="", help="按用例名关键字筛选")
    parser.add_argument(
        "--replay",
        metavar="ASC_FILE",
        default=None,
        help="回放真实 CAN 日志（.asc）——用完整回放链驱动并输出报告",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.doctor:
        return doctor()

    if args.replay:
        return replay_log(args.replay)

    cmd = [sys.executable, "-m", "pytest", "-q"]
    tests_dir = _repo_tests_dir()
    if tests_dir is not None:
        cmd += [str(tests_dir)]
    if args.level == "smoke":
        cmd += ["-m", "smoke"]
    if args.keyword:
        cmd += ["-k", args.keyword]
    if not args.no_report:
        cmd += ["--html=report.html", "--self-contained-html"]
    if args.allure:
        cmd += ["--alluredir=allure-results"]
    if args.junitxml:
        cmd += ["--junitxml=reports/junit.xml"]
    if args.coverage:
        cmd += ["--cov=tcms", "--cov-report=term", "--cov-report=html:htmlcov"]

    print(" ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":  # pragma: no cover —— 入口样板，测试直接调 main()
    raise SystemExit(main())
