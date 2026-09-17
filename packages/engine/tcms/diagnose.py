"""环境自检（doctor）：一次命令回答"这台机器能跑 TCMS-CAN-Test 吗"。

设计动机：
    - 平台/框架类项目最常见的上手障碍是环境不一致（依赖缺失、版本漂移、
      python-can 接口不可用、数据资产没装上）。与其让用户第一个用例就
      撞 ImportError，不如 `run.py --doctor`（或 `tcms-test --doctor`）
      直接输出 PASS/FAIL 表。
    - 为 HIL Roadmap 就绪：无硬件机器上每天也能演示"检测到未配置硬件，
      当前走 virtual"；插卡后同一命令应显示硬件接口可用。

用法:
    from tcms import diagnose
    rows = diagnose.run()          # list[CheckRow]
    diagnose.render(rows)          # 打印表格
"""

from __future__ import annotations

import importlib.metadata
import platform
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import _version, bus, protocol


# python-can 文档推荐探测：列出本机可用的 CAN 接口配置。
# Windows 下探测全部驱动 DLL 较慢（~4s），故以 lru_cache 缓存（进程内一次），
# 且仅无硬件配置分支需要时惰性触发——避免拖慢每次 --doctor。
@lru_cache(maxsize=1)
def _available_interfaces() -> tuple[str, ...]:
    try:
        import contextlib

        # detect_available_configs 在无硬件机会向 stderr 打印驱动探测噪音
        # （Kvaser/Vector/ICS 等 DLL 缺失），自检场景静默收集。
        import io

        import can

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            configs = can.detect_available_configs()
        return tuple(sorted({c.get("interface", "?") for c in configs})) if configs else ()
    except Exception:
        return ()


@dataclass
class CheckRow:
    """单项自检结果。"""

    name: str
    ok: bool
    detail: str = ""
    hint: str = ""


def _pkg_version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "缺失"


def run(env: dict | None = None, verbose: bool = False) -> list[CheckRow]:
    """执行全部自检，返回结果行（不打印）。"""
    rows: list[CheckRow] = []

    # 1. 运行时与依赖版本
    rows.append(
        CheckRow(
            "python",
            True,
            f"{platform.python_implementation()} {platform.python_version()} ({platform.platform()})",
        )
    )
    for dist in ("python-can", "cantools", "pyyaml", "pytest"):
        ver = _pkg_version(dist)
        rows.append(CheckRow(dist, ver != "缺失", ver))

    # 2. 版本一致性：包版本 = pyproject dynamic 单源
    meta_ver = _pkg_version("tcms-can-test")
    rows.append(
        CheckRow(
            "version",
            meta_ver == "缺失" or meta_ver == _version.__version__,
            f"code={_version.__version__} installed={meta_ver}",
            "pip install -e . 后 installed 应与 code 一致",
        )
    )

    # 3. 数据资产可寻址（wheel 分发核心）
    try:
        db = protocol.load_database()
        nmsg = len(db.messages)
        rows.append(CheckRow("dbc", True, f"内置 DBC 解析 OK（{nmsg} 报文）"))
    except Exception as exc:  # noqa: BLE001 —— 自检必须吞异常并报告
        rows.append(CheckRow("dbc", False, repr(exc), "重装包：pip install -e ."))
    try:
        from . import faultdb

        entries = faultdb.load_fault_dictionary().report()["total"]
        rows.append(CheckRow("faults", True, f"FMEA 字典 OK（{entries} 条）"))
    except Exception as exc:  # noqa: BLE001
        rows.append(CheckRow("faults", False, repr(exc), "重装包：pip install -e ."))

    # 4. 虚拟总线可用性（确定性回归基础）
    try:
        b = bus.make_bus(env, receive_own_messages=True)
        b.shutdown()
        rows.append(CheckRow("virtual-bus", True, "virtual 总线可创建"))
    except Exception as exc:  # noqa: BLE001
        rows.append(CheckRow("virtual-bus", False, repr(exc), "检查 python-can 安装"))

    # 5. HIL 硬件状态（Roadmap：真实总线接入）
    cfg = bus.bus_config(env)
    if bus.is_hardware_configured(env):
        rows.append(
            CheckRow(
                "hardware",
                True,
                f"已配置 TCMS_BUS_INTERFACE={cfg['interface']} "
                f"channel={cfg['channel']} bitrate={cfg['bitrate']}",
                "插卡后运行 pytest -m hardware 做真实总线回归",
            )
        )
    else:
        avail = _available_interfaces()
        hint = "可用接口: " + ", ".join(avail) if avail else "未检测到硬件接口"
        rows.append(
            CheckRow(
                "hardware",
                False,
                "未配置硬件（当前走 virtual）",
                f"{hint}。设置 TCMS_BUS_INTERFACE/CHANNEL/BITRATE 接入真实 CAN 卡",
            )
        )

    # 6. 场景资产（仓库场景目录，非打包必需）
    repo_scenarios = Path(__file__).resolve().parent.parent / "scenarios"
    if repo_scenarios.is_dir():
        n = len(list(repo_scenarios.glob("*.yaml")))
        rows.append(CheckRow("scenarios", True, f"{n} 个 YAML 场景可用（{repo_scenarios.name}/）"))
    else:
        rows.append(
            CheckRow(
                "scenarios",
                False,
                f"场景目录缺失: {repo_scenarios}",
                "仓库根下需有 scenarios/（示例数据）",
            )
        )

    if verbose:
        for r in rows:
            print(f"{'PASS' if r.ok else 'FAIL':4} {r.name:14} {r.detail}")
            if not r.ok and r.hint:
                print(f"     ↳ {r.hint}")
    return rows


def render(rows: list[CheckRow]) -> None:
    """打印自检结果表。"""
    width = max(len(r.name) for r in rows) + 2
    print("TCMS-CAN-Test 环境自检")
    print("-" * 60)
    for r in rows:
        mark = "PASS" if r.ok else "FAIL"
        print(f"{mark} {r.name:<{width}} {r.detail}")
        if not r.ok and r.hint:
            print(f"     ^ {r.hint}")
    failed = [r for r in rows if not r.ok]
    print("-" * 60)
    if failed:
        print(f"自检未全通过：{len(failed)} 项待处理（不影响 virtual 模式演示）")
    else:
        print("自检全部通过 —— 环境就绪")


def all_ok(rows: list[CheckRow]) -> bool:
    return all(r.ok for r in rows)
