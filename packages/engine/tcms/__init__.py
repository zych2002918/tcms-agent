"""TCMS-CAN-Test — 列车网络控制（TCMS）CAN 报文自动化测试平台。

包内提供模块级公共 API（稳定入口，跨版本演进）：

    import tcms
    tcms.__version__                 # 版本号（与 pyproject/CHANGELOG 单源）
    tcms.load_database()             # 加载打包内置 DBC（tcms/tcms.dbc）
    tcms.load_fault_dictionary()     # 加载打包内置 FMEA 故障字典（faults.yaml）
    tcms.scenarios.run_yaml(path)    # 一键执行声明式故障场景
    tcms.bus.make_bus()              # 环境变量驱动的总线工厂（HIL 可切硬件）

数据资产（DBC / FMEA 字典 / 场景样例）随 wheel 分发，`pip install` 后开箱可用。
"""

# 常用公共入口的顶层别名（细节演进在子模块内，import tcms 即可起步）
from . import (  # noqa: F401  (public API re-export)
    _version,
    bus,
    cli,
    diagnose,
    faultdb,
    protocol,
    replay,
    scenarios,
)
from ._version import __version__

load_database = protocol.load_database
load_fault_dictionary = faultdb.load_fault_dictionary
make_bus = bus.make_bus

__all__ = [
    "__version__",
    "bus",
    "cli",
    "diagnose",
    "faultdb",
    "protocol",
    "replay",
    "scenarios",
    "load_database",
    "load_fault_dictionary",
    "make_bus",
]
