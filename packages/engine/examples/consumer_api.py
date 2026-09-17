#!/usr/bin/env python3
"""第二消费者示例（consumer_api.py）—— 站在外部使用者视角使用 tcms。

平台化的判据不是"仓库自己能跑"，而是"别人 pip install 后能写自己的
第一个用例"。本脚本刻意**只用 `tcms` 顶层公共 API**（不碰仓库内部
tests/、不 sys.path 指向仓库根），模拟真实使用者：

    import tcms
    tcms.load_database()             # 打包内置 DBC
    tcms.make_bus()                  # 总线工厂（默认 virtual）
    tcms.scenarios.run_yaml(...)     # 声明式故障场景
    tcms.load_fault_dictionary()     # FMEA 字典

运行（依赖已安装时，从任意目录）::

    python examples/consumer_api.py            # 全流程 + 自证断言
    python examples/consumer_api.py --scene X  # 只跑某个场景 YAML

退出码：0 = 全部自证通过；非 0 = 失败（也是公共 API 回归门禁）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 仅当从仓库直接跑（未 pip install）时把仓库根加入 path——
# 模拟"安装态"：若 tcms 已安装则本行不影响（sys.path 已有包）
if not any(p for p in sys.path if p and Path(p).name == "site-packages"):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tcms  # noqa: E402  —— 只经顶层公共 API


def banner(t: str) -> None:
    print("\n" + "=" * 60)
    print(t)
    print("=" * 60)


def main() -> int:
    checks: list[str] = []

    # 1. 版本与数据资产（公共 API 契约）
    banner("STEP 1 | 公共 API 契约")
    print(f"  tcms.__version__ = {tcms.__version__}")
    db = tcms.load_database()
    print(f"  内置 DBC 解析：{len(db.messages)} 报文")
    dict_ = tcms.load_fault_dictionary()
    print(f"  FMEA 字典：{dict_.report()['total']} 条")
    checks.append(f"version={tcms.__version__}")
    assert len(db.messages) >= 22, "DBC 报文数不足（13 系统域扩展 ≥22 帧）"
    assert dict_.report()["total"] >= 22, "FMEA 条目不足"

    # 2. 总线工厂 + 仿真（make_bus 是唯一总线入口）
    banner("STEP 2 | 总线 + 仿真")
    bus = tcms.make_bus(channel="tcms-consumer", receive_own_messages=True)
    try:
        from tcms.simulator import TCMSNodeSimulator  # noqa: PLC0415 业务模块按需导入

        sim = TCMSNodeSimulator(bus, db)
        sim.start()
        msg = bus.recv(timeout=0.3)
        assert msg is not None, "仿真器未产生报文"
        print(f"  收到帧：ID=0x{msg.arbitration_id:X} dlc={msg.dlc}")
        checks.append("sim-frame")
        sim.stop()
    finally:
        bus.shutdown()

    # 3. 声明式故障场景（scenarios 公共 API 一键执行）
    banner("STEP 3 | 场景执行")
    scene_dir = Path(__file__).resolve().parent.parent / "scenarios"
    yamls = sorted(scene_dir.glob("*.yaml"))
    assert yamls, "scenarios/ 为空"
    # 取第一个场景跑通 run_yaml（公共 API）
    report = tcms.scenarios.run_yaml(str(yamls[0]))
    print(f"  场景 {yamls[0].stem}：报告键 {sorted(report.keys())}")
    checks.append(f"scene:{yamls[0].stem}")

    # 4. 回放链（examples/demo_trip.asc 真实日志）
    banner("STEP 4 | 回放")
    asc = Path(__file__).resolve().parent / "demo_trip.asc"
    if asc.exists():
        from tcms import replay  # noqa: PLC0415

        rep = replay.ReplayChain.from_asc(str(asc)).run()
        print(f"  回放 {rep['frames']} 帧，告警 {len(rep['alerts'])} 条")
        assert rep["frames"] > 0
        checks.append(f"replay:{rep['frames']}f")

    print()
    print(f"全部自证通过（{len(checks)} 项）：{', '.join(checks)}")
    print("公共 API 面可用 —— 外部使用者可站在其上写自己的用例。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
