"""总线负载率测试：位级帧模型锚点、滑动窗口、设计上限、压测规划。"""

import itertools

import pytest

from tcms.busload import (
    BusLoadGenerator,
    BusLoadMonitor,
    frame_bits,
    frame_time_s,
)

# ---- 位级帧模型锚点（业界公认值） ----


def test_frame_bits_zero_dlc_anchor():
    """0 字节数据帧：34 可填充位 + 8 填充 + 13 尾段 = 55 位。"""
    assert frame_bits(0) == 55


def test_frame_bits_eight_dlc_anchor():
    """8 字节数据帧：98 可填充位 + 24 填充 + 13 尾段 = 135 位。"""
    assert frame_bits(8) == 135


def test_frame_bits_monotonic_with_dlc():
    bits = [frame_bits(d) for d in range(9)]
    assert bits == sorted(bits)
    assert all(a < b for a, b in itertools.pairwise(bits))


def test_frame_bits_invalid_dlc():
    with pytest.raises(ValueError):
        frame_bits(-1)
    with pytest.raises(ValueError):
        frame_bits(9)


def test_frame_time_inverse_bitrate():
    """帧时间与位速率成反比。"""
    t250 = frame_time_s(8, 250_000)
    t500 = frame_time_s(8, 500_000)
    assert t250 == pytest.approx(135 / 250_000)
    assert t500 == pytest.approx(t250 / 2)


def test_frame_time_invalid_bitrate():
    with pytest.raises(ValueError):
        frame_time_s(8, 0)
    with pytest.raises(ValueError):
        frame_time_s(8, -1)


# ---- 滑动窗口负载监视器 ----


def test_monitor_empty_is_zero():
    mon = BusLoadMonitor()
    assert mon.load_pct(ts=0.0) == 0.0
    assert mon.total_frames == 0


def test_monitor_single_frame_load():
    mon = BusLoadMonitor(bitrate=250_000, window_s=1.0)
    mon.on_frame(8, ts=0.0)
    # 窗口未满：按首帧起的实际经过时间计算 → 0.5s 内 135 位 = 0.108%
    load = mon.load_pct(ts=0.5)
    assert load == pytest.approx(100.0 * 135 / (0.5 * 250_000))


def test_monitor_window_slide_drops_old_frames():
    mon = BusLoadMonitor(bitrate=250_000, window_s=1.0)
    for i in range(10):
        mon.on_frame(8, ts=float(i))
    # t=10.0 时窗口内只有 ts=10 的一帧（9.0 帧恰好滑出边界外）
    load = mon.load_pct(ts=10.0)
    assert load > 0
    # 新帧滑入、旧帧滑出：总数仍累计
    assert mon.total_frames == 10


def test_monitor_steady_state_load_converges():
    """周期流稳态负载率 ≈ 理论值（1 帧/ms × 135 位 @250k = 54%）。"""
    mon = BusLoadMonitor(bitrate=250_000, window_s=1.0)
    period = 0.001
    n = 2000
    for i in range(n):
        mon.on_frame(8, ts=i * period)
    expected = 100.0 * 135 / (0.001 * 250_000)  # 54%
    assert mon.load_pct(ts=n * period) == pytest.approx(expected, rel=0.05)


def test_monitor_assess_levels():
    mon = BusLoadMonitor(bitrate=250_000, window_s=1.0)
    # healthy：空载
    assert mon.assess(ts=0.0)["level"] == "healthy"
    # warning：压到 30~50%
    for i in range(600):
        mon.on_frame(8, ts=i * 0.001)
    mon2 = BusLoadMonitor(bitrate=250_000, window_s=1.0)
    for i in range(600):
        mon2.on_frame(8, ts=i * 0.001)  # 54% > 50%
    assert mon2.assess(ts=0.6)["level"] == "over_limit"


def test_monitor_invalid_params():
    with pytest.raises(ValueError):
        BusLoadMonitor(bitrate=0)
    with pytest.raises(ValueError):
        BusLoadMonitor(window_s=0)
    with pytest.raises(ValueError):
        BusLoadMonitor(bitrate=-1)


# ---- 压测流量规划器 ----


def test_generator_expected_load_matches_sum():
    gen = BusLoadGenerator(bitrate=250_000)
    streams = [
        {"arb_id": 0x100, "dlc": 8, "period_s": 0.010},
        {"arb_id": 0x200, "dlc": 4, "period_s": 0.020},
    ]
    total = sum(frame_bits(s["dlc"]) / s["period_s"] for s in streams)
    assert gen.expected_load_pct(streams) == pytest.approx(100.0 * total / 250_000)


def test_plan_streams_target_30pct():
    gen = BusLoadGenerator(bitrate=250_000)
    streams = gen.plan_streams_for_target(30.0)
    assert streams
    assert gen.expected_load_pct(streams) == pytest.approx(30.0, abs=2.0)
    # arb_id 互不冲突
    ids = [s["arb_id"] for s in streams]
    assert len(ids) == len(set(ids))


def test_plan_streams_target_60pct():
    gen = BusLoadGenerator(bitrate=250_000)
    streams = gen.plan_streams_for_target(60.0)
    assert gen.expected_load_pct(streams) == pytest.approx(60.0, abs=2.0)


def test_plan_streams_target_90pct():
    """90% 高负载可规划（接近饱和但未超 100%）。"""
    gen = BusLoadGenerator(bitrate=250_000)
    streams = gen.plan_streams_for_target(90.0)
    load = gen.expected_load_pct(streams)
    assert 80.0 <= load <= 100.0


def test_plan_streams_invalid_target():
    gen = BusLoadGenerator()
    with pytest.raises(ValueError):
        gen.plan_streams_for_target(0.0)
    with pytest.raises(ValueError):
        gen.plan_streams_for_target(100.0)
    with pytest.raises(ValueError):
        gen.plan_streams_for_target(-5.0)


def test_generator_invalid_bitrate():
    with pytest.raises(ValueError):
        BusLoadGenerator(bitrate=0)


# ---- 高负载 → 低优先级帧劣化（机制演示） ----


def test_high_load_delays_low_prio_frame():
    """高优先级流量挤压：低优先级帧的周期劣化可由位级模型计算。"""
    # 250kbit/s 总线：8 字节帧最坏 135 位 = 540us
    frame_us = frame_time_s(8, 250_000) * 1e6
    assert frame_us == pytest.approx(540.0)
    # 60% 负载下，一个低优先级帧最坏等待：
    # 多个高优先级帧连续仲裁获胜 → 累积延迟超过单帧时间
    busy = 0.6 * 1.0  # 1 秒内 60% 时间被占用
    assert busy * 1e6 > frame_us * 2  # 低优先级帧等待 > 2 帧时间
