"""周期抖动/漂移统计测试：间隔统计/迟到事件/漂移 ppm/告警。"""

import pytest

from tcms import jitter as jt

# ---- 基本统计 ----


def test_no_frames_empty_stats():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    s = jm.stats()
    assert s["count"] == 0 and s["mean"] is None


def test_first_frame_no_interval():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.0)
    assert jm.stats()["count"] == 0


def test_regular_intervals():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    for ts in (0.0, 0.1, 0.2, 0.3):
        jm.observe(ts)
    s = jm.stats()
    assert s["count"] == 3
    assert s["min"] == pytest.approx(0.1) and s["max"] == pytest.approx(0.1)
    assert s["mean"] == pytest.approx(0.1)


def test_jitter_mean_and_stdev():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    for ts in (0.0, 0.09, 0.21, 0.29):  # 间隔 0.09/0.12/0.08
        jm.observe(ts)
    s = jm.stats()
    assert s["count"] == 3
    assert s["mean"] == pytest.approx((0.09 + 0.12 + 0.08) / 3)


# ---- 迟到事件 ----


def test_late_event_counted():
    jm = jt.JitterMonitor(nominal_period_s=0.1)  # 容忍 20% → 上限 0.12
    jm.observe(0.0)
    jm.observe(0.1)  # 正常
    jm.observe(0.23)  # 0.13 > 0.12：迟到
    assert jm.late_events == 1


def test_tolerance_boundary():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.0)
    jm.observe(0.12)  # 恰好上限：不算迟到
    assert jm.late_events == 0
    jm.observe(0.241)  # 0.121 > 0.12：迟到
    assert jm.late_events == 1


# ---- 漂移 ppm ----


def test_drift_zero_when_exact():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.0)
    jm.observe(0.1)
    assert jm.drift_ppm() == pytest.approx(0.0)


def test_drift_positive_slow_clock():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.0)
    jm.observe(0.1001)  # 慢 0.1ms → 1000ppm
    assert jm.drift_ppm() == pytest.approx(1000.0)


def test_drift_negative_fast_clock():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.0)
    jm.observe(0.0999)  # 快 0.1ms → -1000ppm
    assert jm.drift_ppm() == pytest.approx(-1000.0)


def test_drift_alarm_threshold():
    jm = jt.JitterMonitor(nominal_period_s=0.1)  # 阈值 200ppm
    jm.observe(0.0)
    jm.observe(0.10002)  # 200ppm：恰好不告警
    assert not jm.drift_alarm()
    jm.observe(0.20005)  # 平均 (0.10002+0.10003)/2 ≈ 250ppm：告警
    assert jm.drift_alarm()


# ---- 异常与重置 ----


def test_timestamp_rollback_raises():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.2)
    with pytest.raises(ValueError):
        jm.observe(0.1)


def test_reset_clears():
    jm = jt.JitterMonitor(nominal_period_s=0.1)
    jm.observe(0.0)
    jm.observe(0.13)  # 迟到 1 次
    jm.reset()
    assert jm.late_events == 0
    assert jm.stats()["count"] == 0
    assert jm.drift_ppm() == 0.0
