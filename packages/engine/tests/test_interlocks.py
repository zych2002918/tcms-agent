"""列车安全联锁逻辑测试：门-车联锁、超速-制动联锁、受电弓异常、能源联锁。"""

import pytest

from tcms import interlocks as il

CLOSED, OPEN, FAULT, UNKNOWN = 0, 1, 2, 3


# ---- 门-车联锁 ----


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize(
    "door_states,speed,valid,expected",
    [
        ([CLOSED, CLOSED, CLOSED, CLOSED], 80.0, True, False),  # 全关+移动：正常
        ([OPEN, CLOSED, CLOSED, CLOSED], 80.0, True, True),  # 门开+移动：违规
        ([CLOSED, FAULT, CLOSED, CLOSED], 80.0, True, True),  # 门故障+移动：按未关处理
        ([OPEN, CLOSED, CLOSED, CLOSED], 0.0, True, False),  # 静止+门开：停车开门允许
        ([FAULT, CLOSED, CLOSED, CLOSED], 0.0, True, False),  # 静止+门故障：不发车联锁由发车环节管
        ([CLOSED, CLOSED, CLOSED, CLOSED], 0.4, True, False),  # 速度低于移动阈值：不违规
        ([CLOSED, CLOSED, CLOSED, CLOSED], 0.6, True, False),  # 全关+微动：不违规
        ([OPEN, CLOSED, CLOSED, CLOSED], 0.6, True, True),  # 门开+微动：违规
    ],
)
def test_door_motion_conflict(door_states, speed, valid, expected):
    violation, reason = il.door_motion_conflict(door_states, speed, valid)
    assert violation is expected
    if expected:
        assert reason in ("door_open_while_moving", "door_fault_while_moving")


def test_door_fault_reason_is_fault_specific():
    _, reason = il.door_motion_conflict([CLOSED, FAULT, CLOSED, CLOSED], 100.0, True)
    assert reason == "door_fault_while_moving"


def test_door_open_reason_is_open_specific():
    _, reason = il.door_motion_conflict([OPEN, CLOSED, CLOSED, CLOSED], 100.0, True)
    assert reason == "door_open_while_moving"


# ---- 超速-制动联锁 ----


@pytest.mark.parametrize(
    "speed,valid,expected",
    [
        (159.9, True, False),
        (160.0, True, False),  # 等于限速：不触发
        (160.1, True, True),
        (200.0, True, True),
        (200.0, False, False),  # 速度无效：不触发
        (0.0, True, False),
    ],
)
def test_overspeed_trigger(speed, valid, expected):
    assert il.overspeed_trigger(speed, valid) is expected


def test_overspeed_custom_limit():
    assert il.overspeed_trigger(120.0, True, limit=100.0) is True


# ---- 紧急制动决策 ----


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize(
    "overspeed,door_conflict,expected",
    [
        (False, False, False),  # 正常
        (True, False, True),  # 超速
        (False, True, True),  # 门冲突
        (True, True, True),  # 双触发
    ],
)
def test_emergency_brake_decision(overspeed, door_conflict, expected):
    assert il.emergency_brake_decision(100.0, overspeed, door_conflict) is expected


# ---- 受电弓异常 ----


@pytest.mark.parametrize(
    "up,voltage,expected",
    [
        (0, 0.0, False),  # 弓降：无风险
        (1, 25000.0, False),  # 弓升+正常电压
        (1, 18999.0, True),  # 弓升+低电压
        (1, 31001.0, True),  # 弓升+高电压
        (1, 19000.0, False),  # 边界：19000 正常
        (1, 31000.0, False),  # 边界：31000 正常
    ],
)
def test_pantograph_arc_risk(up, voltage, expected):
    risk, reason = il.pantograph_arc_risk(up, voltage)
    assert risk is expected
    if expected:
        assert reason.startswith("line_voltage_")


# ---- 能源联锁 ----


@pytest.mark.parametrize(
    "soc,charging,expected",
    [
        (9, False, True),  # SOC 过低且未充电
        (9, True, False),  # SOC 过低但充电中
        (10, False, False),  # 边界：10 不触发
        (50, False, False),  # 正常
    ],
)
def test_soc_low_charge_guard(soc, charging, expected):
    guard, _ = il.soc_low_charge_guard(soc, charging)
    assert guard is expected


# ---- 牵引-制动互锁 ----


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize(
    "handle,brake,allowed,expected",
    [
        (0, False, True, False),  # 手柄零位+无制动：正常
        (5, False, True, False),  # 牵引无制动：正常
        (0, True, True, False),  # 制动无牵引：正常
        (5, True, True, True),  # 牵引+制动：冲突
        (5, True, False, True),  # 牵引+制动+牵引未允许：冲突
        (5, False, False, True),  # 牵引但未允许（联锁禁止牵引）
    ],
)
def test_traction_brake_conflict(handle, brake, allowed, expected):
    conflict, reason = il.traction_brake_conflict(handle, brake, allowed)
    assert conflict is expected
    if expected:
        assert reason in ("traction_brake_conflict", "traction_not_allowed")


def test_traction_brake_conflict_reasons():
    _, r1 = il.traction_brake_conflict(3, True, True)
    assert r1 == "traction_brake_conflict"
    _, r2 = il.traction_brake_conflict(3, False, False)
    assert r2 == "traction_not_allowed"


# ---- 方向-速度联动 ----


@pytest.mark.parametrize(
    "direction,speed,expected",
    [
        (1, 80.0, False),  # 正向+速度：正常
        (2, -80.0, False),  # 反向+负速度：正常
        (1, 0.0, False),  # 正向+静止：正常
        (0, 80.0, True),  # 中立+移动：违规
        (3, 80.0, True),  # 无效方向+移动：违规
        (0, 0.4, False),  # 中立+微动（低于阈值）：不违规
        (4, 0.0, True),  # 非法方向值：违规
    ],
)
def test_direction_speed_conflict(direction, speed, expected):
    conflict, reason = il.direction_speed_conflict(direction, speed)
    assert conflict is expected
    if expected:
        assert reason in ("direction_neutral_with_motion", "invalid_direction")


# ---- 车门-站台联动 ----


@pytest.mark.parametrize(
    "speed,aligned,expected",
    [
        (0.0, True, False),  # 零速+对准：可开门
        (0.0, False, False),  # 零速+未对准：不开门（无违规，只是未满足条件）
        (80.0, False, False),  # 移动+未对准：不开门
        (80.0, True, True),  # 移动+对准：违规（未停稳开门）
        (0.4, True, False),  # 微动+对准：低于阈值不算移动
        (0.6, True, True),  # 微动+对准：超过阈值违规
    ],
)
def test_platform_door_release(speed, aligned, expected):
    violation, reason = il.platform_door_release(speed, aligned)
    assert violation is expected
    if expected:
        assert reason == "platform_door_release_moving"
