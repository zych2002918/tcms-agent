"""硬件接口层测试：配置读取、工厂创建、hardware marker 隔离。"""

import pytest

from tcms import bus as busmod


def test_default_config_is_virtual():
    cfg = busmod.bus_config(env={})
    assert cfg["interface"] == "virtual"
    assert cfg["channel"] == "tcms-default"
    assert cfg["bitrate"] == 250_000


def test_config_reads_environment():
    env = {
        "TCMS_BUS_INTERFACE": "pcan",
        "TCMS_BUS_CHANNEL": "PCAN_USBBUS1",
        "TCMS_BUS_BITRATE": "500000",
    }
    cfg = busmod.bus_config(env)
    assert cfg == {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 500_000}


def test_is_hardware_configured():
    assert busmod.is_hardware_configured(env={}) is False
    assert busmod.is_hardware_configured(env={"TCMS_BUS_INTERFACE": "socketcan"}) is True


def test_make_bus_virtual_works():
    b = busmod.make_bus(env={}, channel="tcms-test-xyz", receive_own_messages=True)
    try:
        assert "tcms-test-xyz" in str(b.channel_info)
    finally:
        b.shutdown()


def test_make_bus_overrides_config():
    b = busmod.make_bus(
        env={"TCMS_BUS_INTERFACE": "pcan"}, interface="virtual", channel="tcms-test-ovr"
    )
    try:
        assert "tcms-test-ovr" in str(b.channel_info)
    finally:
        b.shutdown()


def test_make_bus_invalid_bitrate_rejected():
    with pytest.raises(ValueError):
        busmod.make_bus(env={"TCMS_BUS_BITRATE": "0"})


def test_make_bus_negative_bitrate_rejected():
    with pytest.raises(ValueError):
        busmod.make_bus(env={"TCMS_BUS_BITRATE": "-100"})


@pytest.mark.hardware
def test_hardware_marker_example():
    """真实硬件用例示例：插卡后 `pytest -m hardware` 才执行。"""
    if not busmod.is_hardware_configured():
        pytest.skip("未配置真实 CAN 硬件（TCMS_BUS_INTERFACE 非 virtual）")
    b = busmod.make_bus()
    assert b is not None
    b.shutdown()
