"""元数据契约测试：版本单一真源 + 打包数据资产可寻址。

防"版本口径漂移"（历史上 pyproject/CHANGELOG/run.py 三处各写各的）：
    - tcms._version.__version__  ==  tcms.__version__
    - pyproject [project].dynamic 声明 version（不再手写静态版本）
    - pyproject 包数据包含 *.dbc / *.yaml（wheel 分发后可 import 即用）

打包数据资产（DBC / FMEA 字典）必须能经 importlib.resources 读出，
保证 `pip install tcms-can-test`（非 editable）后开箱可用——CI 另有
wheel 安装冒烟 job 实证（scripts/check_dist.py）。
"""

import re
from pathlib import Path

import pytest

# tomllib 为 3.11+ 标准库；3.10 需回退 tomli（pyproject [test] extra 已声明）
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 仅 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"

import tcms  # noqa: E402
from tcms import _version, protocol  # noqa: E402

# ---- 版本单一真源 ----


def test_version_single_source():
    """tcms.__init__ 导出的版本与 _version 模块一致。"""
    assert tcms.__version__ == _version.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", tcms.__version__)


def test_pyproject_version_is_dynamic():
    """pyproject 不得再写死静态版本（防双源漂移）。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"].get("version") is None, "静态 version 应从 pyproject 移除"
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"]["attr"] == ("tcms._version.__version__")


def test_pyproject_package_data_covers_assets():
    """DBC 与故障字典必须随包分发。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pkg_data = data["tool"]["setuptools"]["package-data"]["tcms"]
    assert "*.dbc" in pkg_data
    assert "*.yaml" in pkg_data


# ---- 打包数据资产可寻址 ----


def test_package_dbc_resource_loads():
    """内置 DBC 经 importlib.resources 可读且能解析为数据库。"""
    db = protocol.load_database()  # 缺省 = 包资源路径
    assert db is not None
    # 解析出的 8 个核心报文名与常量表一致
    names = {m.name for m in db.messages}
    for expect in protocol.MESSAGE_NAMES.values():
        assert expect in names, f"内置 DBC 缺少报文 {expect}"


def test_faults_yaml_resource_exists():
    """FMEA 字典 yaml 作为包数据存在（faultdb 加载路径即包内）。"""
    from tcms import faultdb

    assert faultdb.FAULTS_YAML.exists(), "faults.yaml 未随包分发"
    entries = faultdb.load_fault_dictionary().report()["total"]
    assert entries >= 22


def test_public_api_contract():
    """顶层公共 API（平台化契约）在包内可导入。"""
    assert callable(tcms.load_database)
    assert callable(tcms.load_fault_dictionary)
    assert callable(tcms.make_bus)
    for mod in ("bus", "faultdb", "protocol", "scenarios", "cli", "diagnose", "replay"):
        assert getattr(tcms, mod, None) is not None


def test_dbc_path_points_inside_package():
    """DBC_PATH 必须指向包内资源（不再指向仓库根 dbc/）。"""
    dbc = Path(str(protocol.DBC_PATH))
    assert dbc.suffix == ".dbc"
    # 包目录（tcms/）下，而非仓库根
    assert "tcms" in dbc.parts
    if dbc.exists():  # 常规目录安装下真实存在
        assert dbc.is_file()


@pytest.mark.smoke
def test_version_cli_consistency():
    """run.py --version 输出与包版本一致（防 CLI 兜底漂移）。"""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, str(REPO / "run.py"), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0
    assert tcms.__version__ in out.stdout
