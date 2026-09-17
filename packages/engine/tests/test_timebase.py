"""虚拟时间基测试（timebase.py）：统一时间源、推进/跳变、全局替换。"""

import pytest

from tcms import timebase

# ---- VirtualClock 基础 ----


def test_default_mode_is_monotonic():
    clock = timebase.VirtualClock()
    assert clock.mode == timebase.MODE_MONOTONIC
    t1 = clock.now()
    t2 = clock.now()
    assert t2 >= t1


def test_virtual_mode_advance():
    clock = timebase.VirtualClock(mode="virtual")
    assert clock.now() == 0.0
    clock.advance(0.1)
    assert clock.now() == pytest.approx(0.1)
    clock.advance(2.5)
    assert clock.now() == pytest.approx(2.6)


def test_virtual_mode_set():
    clock = timebase.VirtualClock(mode="virtual")
    clock.set(5.0)
    assert clock.now() == 5.0
    clock.set(0.5)
    assert clock.now() == 0.5


def test_virtual_mode_start_offset():
    clock = timebase.VirtualClock(mode="virtual", start=10.0)
    assert clock.now() == 10.0


def test_advance_rejects_negative():
    clock = timebase.VirtualClock(mode="virtual")
    with pytest.raises(ValueError):
        clock.advance(-0.1)


def test_set_rejects_negative():
    clock = timebase.VirtualClock(mode="virtual")
    with pytest.raises(ValueError):
        clock.set(-1.0)


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        timebase.VirtualClock(mode="bogus")


def test_advance_only_in_virtual_mode():
    clock = timebase.VirtualClock()
    with pytest.raises(RuntimeError):
        clock.advance(0.1)


def test_set_only_in_virtual_mode():
    clock = timebase.VirtualClock()
    with pytest.raises(RuntimeError):
        clock.set(1.0)


def test_mode_switch():
    clock = timebase.VirtualClock(mode="virtual", start=3.0)
    clock.set_mode(timebase.MODE_MONOTONIC)
    assert clock.mode == timebase.MODE_MONOTONIC
    clock.set_mode(timebase.MODE_VIRTUAL, start=7.0)
    assert clock.now() == 7.0


def test_monotonic_alias():
    clock = timebase.VirtualClock(mode="virtual")
    clock.advance(0.5)
    assert clock.monotonic() == pytest.approx(0.5)


def test_reset():
    clock = timebase.VirtualClock(mode="virtual")
    clock.advance(4.0)
    clock.reset()
    assert clock.now() == 0.0
    clock.reset(9.0)
    assert clock.now() == 9.0


# ---- 全局时间基 ----


def test_global_clock_singleton():
    c1 = timebase.global_clock()
    c2 = timebase.global_clock()
    assert c1 is c2


def test_install_replaces_global():
    vc = timebase.VirtualClock(mode="virtual")
    installed = timebase.install(vc)
    assert installed is vc
    assert timebase.global_clock() is vc
    timebase.reset_global()


def test_global_monotonic_after_install():
    vc = timebase.VirtualClock(mode="virtual")
    timebase.install(vc)
    vc.advance(2.0)
    assert timebase.monotonic() == pytest.approx(2.0)
    timebase.reset_global()


def test_reset_global_restores_default():
    timebase.install(timebase.VirtualClock(mode="virtual"))
    timebase.reset_global()
    # 重置后全局回到默认 monotonic（不再抛 virtual-only 错误）
    assert timebase.monotonic() >= 0


def test_global_clock_default_is_monotonic():
    c = timebase.global_clock()
    assert c.mode == timebase.MODE_MONOTONIC
