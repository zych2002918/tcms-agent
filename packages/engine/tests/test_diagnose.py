"""环境自检（tcms.diagnose）测试：自检行结构与关键项。

doctor 是平台化"HIL 就绪 + 上手排障"入口，必须可测：
    - 依赖/版本项齐全
    - 内置数据资产可寻址（dbc/faults PASS）
    - virtual 总线可用（PASS）
    - 无硬件配置时 hardware 项为 FAIL（预期信号，非错误）
"""

from tcms import diagnose


def test_run_returns_ordered_checks():
    rows = diagnose.run()
    names = [r.name for r in rows]
    assert names[0] == "python"
    assert "python-can" in names
    assert "cantools" in names
    assert "version" in names
    assert "dbc" in names
    assert "faults" in names
    assert "virtual-bus" in names
    assert "hardware" in names
    assert "scenarios" in names


def test_data_assets_ok():
    rows = diagnose.run()
    by = {r.name: r for r in rows}
    assert by["dbc"].ok
    assert by["faults"].ok
    # 条数随字典扩充演进：断言格式而非写死 22
    import re

    m = re.search(r"(\d+) 条", by["faults"].detail)
    assert m and int(m.group(1)) >= 26


def test_virtual_bus_ok():
    rows = diagnose.run()
    by = {r.name: r for r in rows}
    assert by["virtual-bus"].ok


def test_hardware_unconfigured_is_explicit_fail():
    """无硬件配置时 hardware 项 FAIL 且 hint 说明如何接入（HIL 引导）。"""
    rows = diagnose.run(env={"TCMS_BUS_INTERFACE": "virtual"})
    by = {r.name: r for r in rows}
    assert not by["hardware"].ok
    assert "TCMS_BUS_INTERFACE" in by["hardware"].hint


def test_hardware_configured_shows_config():
    """配置 TCMS_BUS_* 后 hardware 项 PASS 并回显配置。"""
    rows = diagnose.run(env={"TCMS_BUS_INTERFACE": "pcan", "TCMS_BUS_CHANNEL": "PCAN_USBBUS1"})
    by = {r.name: r for r in rows}
    assert by["hardware"].ok
    assert "pcan" in by["hardware"].detail


def test_render_and_all_ok():
    """render 不抛异常；all_ok 判定与 ok 字段一致。"""
    rows = diagnose.run()
    diagnose.render(rows)  # 不抛即可（stdout 输出）
    assert diagnose.all_ok(rows) is False  # 无硬件时必不全通过
    # 人为全 PASS 时应判定全通过
    import dataclasses

    ok_rows = [dataclasses.replace(r, ok=True) for r in rows]
    assert diagnose.all_ok(ok_rows)


def test_run_verbose_no_crash():
    """verbose 分支（打印详情）不抛异常。"""
    rows = diagnose.run(verbose=True)
    assert len(rows) >= 8


# ---- 防御/失败分支（doctor 的排障价值所在） ----


def test_available_interfaces_exception_returns_empty(monkeypatch):
    """驱动探测抛异常时降级为空列表（不炸 doctor）。"""
    import can

    def _boom(*a, **kw):
        raise RuntimeError("driver probe failed")

    monkeypatch.setattr(can, "detect_available_configs", _boom)
    diagnose._available_interfaces.cache_clear()  # 清 lru_cache，强制走异常路径
    assert diagnose._available_interfaces() == ()
    diagnose._available_interfaces.cache_clear()  # 恢复后续真实探测


def test_pkg_version_missing_returns_label(monkeypatch):
    """未安装的发行版显示'缺失'而非抛错。"""
    import importlib.metadata

    def _missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    assert diagnose._pkg_version("nonexistent-pkg-xyz") == "缺失"


def test_dbc_load_failure_reports_fail(monkeypatch):
    """内置 DBC 加载失败时 dbc 项 FAIL 并带修复提示。"""
    from tcms import protocol

    def _boom(*a, **kw):
        raise RuntimeError("dbc corrupt")

    monkeypatch.setattr(protocol, "load_database", _boom)
    rows = diagnose.run()
    by = {r.name: r for r in rows}
    assert not by["dbc"].ok
    assert "pip install" in by["dbc"].hint


def test_faults_load_failure_reports_fail(monkeypatch):
    """FMEA 字典加载失败时 faults 项 FAIL。"""
    from tcms import faultdb

    def _boom(*a, **kw):
        raise RuntimeError("yaml corrupt")

    monkeypatch.setattr(faultdb, "load_fault_dictionary", _boom)
    rows = diagnose.run()
    by = {r.name: r for r in rows}
    assert not by["faults"].ok


def test_scenarios_dir_missing_reports_fail(monkeypatch):
    """场景目录缺失时 scenarios 项 FAIL（仓库态依赖显式暴露）。"""
    rows = diagnose.run()
    by = {r.name: r for r in rows}

    # 直接构造缺目录环境：monkeypatch is_dir 使 scenarios 探测失败
    from pathlib import Path as _Path

    orig_isdir = _Path.is_dir

    def _fake_isdir(self):
        if str(self).endswith("scenarios"):
            return False
        return orig_isdir(self)

    monkeypatch.setattr(_Path, "is_dir", _fake_isdir)
    rows2 = diagnose.run()
    by2 = {r.name: r for r in rows2}
    assert not by2["scenarios"].ok
    assert by["scenarios"].ok  # 原环境正常


def test_render_all_ok_branch(capsys):
    """render 在全 PASS 时输出'全部通过'结尾。"""
    import dataclasses

    rows = diagnose.run()
    ok_rows = [dataclasses.replace(r, ok=True) for r in rows]
    diagnose.render(ok_rows)
    out = capsys.readouterr().out
    assert "自检全部通过" in out
