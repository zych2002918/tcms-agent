"""CLI 入口（tcms.cli）测试：平台化命令的每个分支必须可测。

覆盖策略：
    - 参数解析各 flag（--level/-k/--allure/--junitxml/--coverage/--no-report）
      通过 monkeypatch subprocess.call 拦截（不真跑全量测试）
    - --replay / --doctor 走真实轻量执行（doctor 有自检行，replay 用小 .asc）
    - build_parser / _repo_tests_dir 直接单测
"""

import subprocess
from pathlib import Path

import pytest

from tcms import cli

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def fake_call(monkeypatch):
    """拦截 subprocess.call，记录命令并返回 0。"""

    def _call(cmd, **kwargs):
        _call.last_cmd = cmd
        return 0

    monkeypatch.setattr(subprocess, "call", _call)
    _call.last_cmd = None
    return _call


def test_version_returns_same_as_package():
    from tcms import __version__

    assert cli._version() == __version__


def test_repo_tests_dir_detected():
    d = cli._repo_tests_dir()
    assert d is not None
    assert d.name == "tests"
    assert d.is_dir()


def test_full_default_build(fake_call):
    """默认 full：应带 tests 目录 + HTML 报告。"""
    rc = cli.main([])
    assert rc == 0
    cmd = " ".join(fake_call.last_cmd)
    assert "tests" in cmd
    assert "--html=report.html" in cmd


def test_smoke_level(fake_call):
    rc = cli.main(["--level", "smoke"])
    assert rc == 0
    assert "-m" in fake_call.last_cmd and "smoke" in fake_call.last_cmd


def test_keyword_flag(fake_call):
    rc = cli.main(["-k", "door"])
    assert rc == 0
    assert "-k" in fake_call.last_cmd and "door" in fake_call.last_cmd


def test_allure_junit_coverage_flags(fake_call):
    rc = cli.main(["--allure", "--junitxml", "--coverage", "--no-report"])
    assert rc == 0
    joined = " ".join(fake_call.last_cmd)
    assert "--alluredir=allure-results" in joined
    assert "--junitxml=reports/junit.xml" in joined
    assert "--cov=tcms" in joined
    assert "--html" not in joined  # --no-report


def test_doctor_exit_ok(monkeypatch):
    """doctor 在完全健康环境下返回 0（用全 ok 行桩）。"""
    import dataclasses

    from tcms import diagnose

    rows = diagnose.run()
    ok_rows = [dataclasses.replace(r, ok=True) for r in rows]

    monkeypatch.setattr(diagnose, "run", lambda: ok_rows)
    monkeypatch.setattr(diagnose, "all_ok", lambda rows: True)
    monkeypatch.setattr(diagnose, "render", lambda rows: None)
    assert cli.main(["--doctor"]) == 0


def test_doctor_exit_fail_when_not_ok(capsys):
    """doctor 有 FAIL 项时返回 1（无硬件场景的真实环境自检）。"""
    rc = cli.main(["--doctor"])
    out = capsys.readouterr().out
    assert rc == 1  # 无硬件 → hardware FAIL → 返回 1
    assert "自检" in out


def test_replay_log_runs(capsys):
    """--replay 用 examples/demo_trip.asc 真实日志跑通完整回放链。"""
    asc = REPO / "examples" / "demo_trip.asc"
    assert asc.exists(), "examples/demo_trip.asc 缺失"
    rc = cli.main(["--replay", str(asc)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "帧回放完成" in out
    assert "EBM" in out


def test_replay_missing_file_fails():
    with pytest.raises(Exception):
        cli.main(["--replay", "no_such_file.asc"])


def test_parser_has_all_options():
    p = cli.build_parser()
    opts = {o.dest for o in p._actions}
    assert {
        "version",
        "doctor",
        "level",
        "allure",
        "no_report",
        "coverage",
        "junitxml",
        "keyword",
        "replay",
    } <= opts


def test_main_returns_int_for_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "tcms-can-test" in capsys.readouterr().out


def test_replay_no_alerts_branch(tmp_path, capsys):
    """replay 无告警分支（57 行）：普通周期帧不触发任何安全事件。"""
    asc = tmp_path / "quiet.asc"
    asc.write_text(
        "date Thu Jan 01 00:00:00 1970\n"
        "base hex  time absolute\n"
        "no internal events logged\n"
        "Begin Triggerblock Thu Jan 01 00:00:00 1970\n"
        "  0.000000 1  100 Rx d 8 00 02 00 00 00 00 00 00\n"  # 心跳帧，NodeStatus=0
        "  0.100000 1  200 Rx d 8 00 00 00 00 00 00 00 00\n"  # 车速 0
        "End TriggerBlock\n",
        encoding="utf-8",
    )
    rc = cli.main(["--replay", str(asc)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "无告警" in out
