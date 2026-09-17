"""超速监督分层与动态 EBI 曲线测试（ATP 速度监督）。"""

import pytest

from tcms import atp as at

NONE, WARNING, SBI, EBI = (
    at.SUPERVISION_NONE,
    at.SUPERVISION_WARNING,
    at.SUPERVISION_SBI,
    at.SUPERVISION_EBI,
)


# ---- 速度监督分层 ----


def test_default_thresholds():
    sup = at.SpeedSupervisor(limit_kmh=160)
    th = sup.thresholds()
    assert th["warning"] == 155.0
    assert th["sbi"] == 158.0
    assert th["ebi"] == 160.0


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize(
    "speed,expected",
    [
        (0.0, NONE),
        (100.0, NONE),
        (154.9, NONE),  # ≤ warning：正常
        (155.0, NONE),  # 边界：等于 warning 不触发（需 >）
        (155.1, WARNING),  # > warning
        (157.9, WARNING),
        (158.0, WARNING),  # 边界：等于 sbi 不触发 → 仍 warning
        (158.1, SBI),
        (159.9, SBI),
        (160.0, SBI),  # 边界：等于 ebi 不触发 → 仍 sbi
        (160.1, EBI),  # > ebi：紧急制动
    ],
)
def test_speed_supervision_levels(speed, expected):
    sup = at.SpeedSupervisor(limit_kmh=160)
    assert sup.evaluate(speed) == expected


def test_invalid_speed_no_supervision():
    sup = at.SpeedSupervisor(limit_kmh=160)
    assert sup.evaluate(200.0, speed_valid=False) == NONE


def test_custom_thresholds():
    sup = at.SpeedSupervisor(limit_kmh=100, warning_offset=10, sbi_offset=5, ebi_offset=1)
    th = sup.thresholds()
    assert th["warning"] == 90.0
    assert th["sbi"] == 95.0
    assert th["ebi"] == 99.0
    assert sup.evaluate(96.0) == SBI
    assert sup.evaluate(99.5) == EBI


# ---- 动态 EBI 曲线 ----


def test_curve_allowed_at_target():
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    assert curve.allowed_at(0) == pytest.approx(30.0)  # 目标点 = 目标速度
    assert curve.allowed_at(900) == pytest.approx(120.0)  # 起点 = 当前速度
    assert curve.allowed_at(450) == pytest.approx(75.0)  # 中点线性


def test_curve_overspeed_check():
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    assert curve.is_overspeed(80.0, 450)  # 中点允许 75：80 超速
    assert not curve.is_overspeed(70.0, 450)


def test_curve_braking_point():
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    bp = curve.braking_point(80.0)
    # d = (80-30)/slope；slope = (120-30)/900 = 0.1 → d = 500
    assert bp == pytest.approx(500.0)


def test_curve_braking_point_below_target():
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    assert curve.braking_point(20.0) == 0.0


def test_curve_errors():
    with pytest.raises(ValueError):
        at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=0)
    with pytest.raises(ValueError):
        at.DynamicEbiCurve(target_speed_kmh=120, current_speed_kmh=30, brake_distance_m=900)
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    with pytest.raises(ValueError):
        curve.allowed_at(-1)


def test_curve_allowed_capped_at_current():
    """allowed_at 不能超过当前允许速度（曲线远端封顶）。"""
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    assert curve.allowed_at(5000) == pytest.approx(120.0)  # 远距离封顶


def test_curve_zero_distance_allowed():
    curve = at.DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    assert curve.allowed_at(0) == pytest.approx(30.0)
