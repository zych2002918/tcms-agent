"""硬件接口层抽离（Bus Factory）—— HIL/台架从 virtual 切真实接口零改代码。

对标真实测试分层：CI 环境用 python-can 的 virtual 接口做确定性回归，
HIL/台架接入真实 CAN 卡（PCAN/Vector/socketcan/slcan）时只改环境变量，
不修改任何业务代码与测试用例——"同一套用例，两种执行环境"。

环境变量（全部可选，缺省 = virtual 本地回环）：
    TCMS_BUS_INTERFACE  接口类型（virtual/pcan/vector/socketcan/slcan/...）
    TCMS_BUS_CHANNEL    通道名（如 PCAN_USBBUS1、can0、COM3）
    TCMS_BUS_BITRATE    位速率 bps（默认 250000）

真实硬件用例用 pytest marker 隔离：
    @pytest.mark.hardware 的用例 CI 默认跳过（pytest.ini 注册 marker），
    本地插卡后 `pytest -m hardware` 显式执行。
"""

import os

import can

DEFAULT_INTERFACE = "virtual"
DEFAULT_CHANNEL = "tcms-default"
DEFAULT_BITRATE = 250_000


def bus_config(env: dict | None = None) -> dict:
    """读取总线配置（可注入 env 便于测试）。"""
    e = os.environ if env is None else env
    return {
        "interface": e.get("TCMS_BUS_INTERFACE", DEFAULT_INTERFACE),
        "channel": e.get("TCMS_BUS_CHANNEL", DEFAULT_CHANNEL),
        "bitrate": int(e.get("TCMS_BUS_BITRATE", str(DEFAULT_BITRATE))),
    }


def make_bus(env: dict | None = None, **overrides) -> can.BusABC:
    """按环境变量创建 CAN 总线；overrides 直接覆盖配置（测试便利）。

    除 interface/channel/bitrate 外，其余覆盖项（如 receive_own_messages）
    原样透传给 can.Bus——与直接 can.Bus(**kwargs) 语义一致。
    """
    cfg = bus_config(env)
    cfg.update(overrides)
    if cfg["bitrate"] <= 0:
        raise ValueError(f"bitrate 必须为正数，got {cfg['bitrate']}")
    return can.Bus(
        interface=cfg["interface"],
        channel=cfg["channel"],
        bitrate=cfg["bitrate"],
        **{k: v for k, v in cfg.items() if k not in ("interface", "channel", "bitrate")},
    )


def is_hardware_configured(env: dict | None = None) -> bool:
    """是否配置了真实硬件接口（virtual 之外）。"""
    return bus_config(env)["interface"] != DEFAULT_INTERFACE
