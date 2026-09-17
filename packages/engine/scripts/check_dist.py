#!/usr/bin/env python3
"""分发自检（Distribution smoke）：验证 wheel 安装后"开箱即用"。

背景：本项目此前 `packages=["tcms"]` 但 DBC 与数据资产在包外，只有
`-e .[test,...]`（editable）安装从未暴露——真实用户 `pip install`
后必然 ImportError/找不到文件。此脚本实证"分发契约"：

    1. tcms 可导入，__version__ 存在且格式合法
    2. 内置 DBC / FMEA 字典经 importlib.resources 可寻址并解析
    3. 顶层公共 API（load_database / load_fault_dictionary / make_bus）
       可用（平台化契约）
    4. 场景样例目录存在（仓库态）

用法（推荐在 CI wheel 安装 job 中，对**非 editable** 安装执行）::

    python scripts/check_dist.py

退出码：0 = 全部通过；1 = 任一项失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"{mark} {name:24} {detail}")
    if not ok:
        FAILED.append(name)


def main() -> int:
    import tcms
    from tcms import faultdb, protocol

    # 1. 包版本
    check("version", hasattr(tcms, "__version__"), tcms.__version__)
    check("version format", len(tcms.__version__.split(".")) == 3, tcms.__version__)

    # 2. 数据资产可寻址（zip/常规安装均须可用）
    try:
        db = protocol.load_database()
        check("dbc asset", True, f"{len(db.messages)} 报文解析成功")
    except Exception as exc:  # noqa: BLE001
        check("dbc asset", False, repr(exc))
    try:
        n = faultdb.load_fault_dictionary().report()["total"]
        check("faults asset", n >= 22, f"{n} 条 FMEA 字典条目")
    except Exception as exc:  # noqa: BLE001
        check("faults asset", False, repr(exc))

    # 3. 公共 API 契约
    check("load_database", callable(getattr(tcms, "load_database", None)))
    check("load_fault_dictionary", callable(getattr(tcms, "load_fault_dictionary", None)))
    check("make_bus", callable(getattr(tcms, "make_bus", None)))
    check("scenarios module", getattr(tcms, "scenarios", None) is not None)

    # 4. 场景样例目录（仓库态数据）
    scenes = Path(__file__).resolve().parent.parent / "scenarios"
    n_scene = len(list(scenes.glob("*.yaml"))) if scenes.is_dir() else 0
    check("scenarios dir", scenes.is_dir() and n_scene >= 5, f"{n_scene} 个 YAML")

    print("-" * 60)
    if FAILED:
        print(f"分发自检未通过：{', '.join(FAILED)}")
        return 1
    print("分发自检全部通过 —— wheel 安装后开箱即用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
