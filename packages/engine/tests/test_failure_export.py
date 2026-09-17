"""失败现场自动导出 hook 验证（P1-8）。

用子进程跑一个"必然失败且注册了 crash_site 的测试"，
断言 conftest.pytest_runtest_makereport 在失败后自动导出：
    reports/failures/<nodeid>/summary.txt + recorder.json + recorder.csv
保证失败现场机制本身被测试覆盖（元测试）。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FAILURES_ROOT = REPO_ROOT / "reports" / "failures"
# 临时测试文件放 tests/_export_tmp（下划线前缀目录默认不被 pytest 收集），
# 子进程 pytest 显式指定文件路径即会收集，且能沿 tests/ 找到 conftest hook
EXPORT_TMP = Path(__file__).resolve().parent / "_export_tmp"

# 子进程内运行的"注定失败"测试源码
_FAILING_TEST = """
import pytest
from tcms.recorder import EventRecorder, EVENT_EBM


def test_will_fail_with_crash_site(crash_site):
    rec = EventRecorder()
    rec.record_event(EVENT_EBM, category="can", message="frame", payload={"id": "0x100"})
    crash_site.register("recorder", rec)
    assert 1 + 1 == 3  # 必然失败
"""


def _run_pytest(test_file: Path) -> subprocess.CompletedProcess:
    """子进程跑 pytest（基于完整环境 + 强制 UTF-8，避免 GBK 解码崩）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "pytest",
            str(test_file),
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _cleanup_tmp() -> None:
    """删除临时测试目录，防止污染下一次独立 pytest 收集。"""
    import shutil

    shutil.rmtree(EXPORT_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_leftover_tmp():
    """模块内任何用例结束后都清掉 _export_tmp（含失败路径）。"""
    yield
    _cleanup_tmp()


def test_failure_export_produces_artifacts():
    """失败用例自动导出 summary + recorder json/csv。"""
    EXPORT_TMP.mkdir(parents=True, exist_ok=True)
    test_file = EXPORT_TMP / "test_will_fail.py"
    test_file.write_text(_FAILING_TEST, encoding="utf-8")
    target = FAILURES_ROOT / "tests__export_tmp_test_will_fail.py__test_will_fail_with_crash_site"
    proc = _run_pytest(test_file)
    assert proc.returncode == 1, proc.stdout  # 子进程测试确实失败
    summary = target / "summary.txt"
    rec_json = target / "recorder.json"
    rec_csv = target / "recorder.csv"
    assert summary.exists(), f"失败摘要未导出: {target}"
    assert rec_json.exists(), "recorder JSON 未导出"
    assert rec_csv.exists(), "recorder CSV 未导出"
    # 内容抽查
    assert "assert 1 + 1 == 3" in summary.read_text(encoding="utf-8")
    body = rec_json.read_text(encoding="utf-8")
    assert "frame" in body and "0x100" in body
    assert "can" in rec_csv.read_text(encoding="utf-8")


def test_passing_test_no_artifacts():
    """通过的用例不产生失败现场。"""
    EXPORT_TMP.mkdir(parents=True, exist_ok=True)
    test_file = EXPORT_TMP / "test_pass.py"
    test_file.write_text(
        "def test_pass(crash_site):\n    crash_site.register('rec', object())\n    assert True\n",
        encoding="utf-8",
    )
    proc = _run_pytest(test_file)
    assert proc.returncode == 0, proc.stdout
    target = FAILURES_ROOT / "tests__export_tmp_test_pass.py__test_pass"
    assert not target.exists() or not any(target.iterdir())
