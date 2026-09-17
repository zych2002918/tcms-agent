"""紧急制动管理（EBM）测试：模式×原因矩阵穷举、缓解/复位闭环、模式迁移、SIL 双通道表决。

测试分五组（对标 交接计划 §1.2）：
1. 矩阵测试：8 原因 × 3 模式，断言处置符合 REASONS 表（制动/不适用/降级）
2. 闭环测试：触发→BRAKE→零速+原因消失→RELEASED；条件不满足保持 BRAKE
3. 复位测试：self_heal 限 1 次，超限需远程复位
4. 模式迁移测试：降级链单步合法，跳级拒绝
5. SIL 测试：SIL4 任一通道触发（故障安全）vs SIL2 双通道一致（防误报）
6. 非法输入：未知模式/未知原因抛 ValueError
"""

import pytest

from tcms import ebm

FAM, CM, RM = ebm.MODE_FAM, ebm.MODE_CM, ebm.MODE_RM


# ---- 1. 矩阵测试：8 原因 × 3 模式 ----

# (原因, 模式, 期望是否适用)
MATRIX = [
    ("overspeed", FAM, True),  # 超速：所有模式均紧急制动
    ("overspeed", CM, True),
    ("overspeed", RM, True),
    ("door_open", FAM, True),  # 门开：FAM/CM 制动；RM 不适用（人工低速工况另行管理）
    ("door_open", CM, True),
    ("door_open", RM, False),
    ("ato_fault", FAM, True),  # ATO 故障：仅 FAM 适用 → 制动+降 CM
    ("ato_fault", CM, False),
    ("ato_fault", RM, False),
    ("atp_fault", FAM, True),  # ATP 故障：FAM/CM 适用 → 制动+降 RM
    ("atp_fault", CM, True),
    ("atp_fault", RM, False),
    ("obstacle", FAM, True),  # 障碍物：FAM/CM 制动；RM 低速由司机处置
    ("obstacle", CM, True),
    ("obstacle", RM, False),
    ("fire_alarm", FAM, True),  # 火灾：所有模式制动
    ("fire_alarm", CM, True),
    ("fire_alarm", RM, True),
    ("maintenance_sw", FAM, True),  # 维护开关：所有模式制动
    ("maintenance_sw", CM, True),
    ("maintenance_sw", RM, True),
    ("hardwire_loss", FAM, True),  # 网络丢失硬线备份：所有模式制动
    ("hardwire_loss", CM, True),
    ("hardwire_loss", RM, True),
]


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize("reason,mode,expected", MATRIX)
def test_matrix_applicability(reason, mode, expected):
    """矩阵穷举：原因×模式 处置是否符合 REASONS 表。"""
    mgr = ebm.EmergencyBrakeManager(mode=mode)
    result = mgr.trigger(reason)
    assert result["applied"] is expected
    assert result["reason"] == reason
    assert result["sil"] == ebm.REASONS[reason]["sil"]
    if expected:
        assert result["action"] == ebm.REASONS[reason]["action"]
        assert mgr.state == ebm.STATE_BRAKE
    else:
        assert result["action"] == "record_only"
        assert result["mode_change"] is None
        assert mgr.state == ebm.STATE_IDLE  # 不适用原因：不误制动


@pytest.mark.parametrize("reason", list(ebm.REASONS))
def test_trigger_returns_action_and_sil(reason):
    """触发结果的动作与 SIL 与原因表一致。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    result = mgr.trigger(reason)
    assert result["action"] == ebm.REASONS[reason]["action"]
    assert result["sil"] == ebm.REASONS[reason]["sil"]
    assert result["applied"] is True


# ---- 2. 闭环测试 ----


@pytest.mark.smoke
@pytest.mark.safety
def test_closed_loop_trigger_brake_release():
    """闭环：触发→BRAKE→喂零速+原因消失→RELEASED。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("overspeed")
    assert mgr.state == ebm.STATE_BRAKE
    mgr.update_reason_status("overspeed", False)  # 外部喂入：超速已消失
    assert mgr.release_condition(0.0) is True  # 零速 + 原因消失
    assert mgr.state == ebm.STATE_RELEASED


def test_release_requires_reason_cleared():
    """原因未消失：即使零速也不缓解，仍保持 BRAKE。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("fire_alarm")
    mgr.update_reason_status("fire_alarm", True)  # 火警仍在
    assert mgr.release_condition(0.0) is False
    assert mgr.state == ebm.STATE_BRAKE


def test_release_requires_zero_speed():
    """速度未归零：即使原因消失也不缓解，仍保持 BRAKE。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("obstacle")
    mgr.update_reason_status("obstacle", False)
    assert mgr.release_condition(30.0) is False
    assert mgr.state == ebm.STATE_BRAKE


def test_zero_speed_threshold_boundary():
    """零速阈值边界：<=0.5 视为零速，>0.5 不算。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM, zero_speed_threshold_kmh=0.5)
    mgr.trigger("obstacle")
    mgr.update_reason_status("obstacle", False)
    assert mgr.release_condition(0.5) is True
    # 重置后再次触发，验证 0.51 不满足
    mgr2 = ebm.EmergencyBrakeManager(mode=FAM)
    mgr2.trigger("obstacle")
    mgr2.update_reason_status("obstacle", False)
    assert mgr2.release_condition(0.51) is False


def test_retrigger_after_release():
    """缓解后再次触发：RELEASED → BRAKE，闭环可重复。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    mgr.release_condition(0.0)
    assert mgr.state == ebm.STATE_RELEASED
    mgr.trigger("overspeed")  # 新事件再次到来
    assert mgr.state == ebm.STATE_BRAKE


# ---- 3. 复位测试 ----


def test_self_heal_first_success():
    """自愈复位第 1 次成功：BRAKE → IDLE。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("maintenance_sw")
    assert mgr.self_heal() is True
    assert mgr.state == ebm.STATE_IDLE


@pytest.mark.safety
def test_self_heal_second_denied_fault():
    """自愈复位第 2 次被拒绝：转入 FAULT，需远程复位。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("maintenance_sw")
    assert mgr.self_heal() is True
    assert mgr.self_heal() is False
    assert mgr.state == ebm.STATE_FAULT


def test_remote_reset_recovers():
    """远程复位后恢复：FAULT → IDLE，且自愈能力重新可用。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.trigger("overspeed")
    mgr.self_heal()
    assert mgr.self_heal() is False  # 超限 → FAULT
    assert mgr.state == ebm.STATE_FAULT
    mgr.reset()  # 远程复位
    assert mgr.state == ebm.STATE_IDLE
    mgr.trigger("overspeed")
    assert mgr.self_heal() is True  # 自愈能力恢复


# ---- 4. 模式迁移测试 ----


def test_mode_chain_fam_cm_rm_legal():
    """降级链 FAM→CM→RM 单步迁移合法。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    mgr.set_mode(CM)
    assert mgr.mode == CM
    mgr.set_mode(RM)
    assert mgr.mode == RM


@pytest.mark.parametrize("start,target", [(RM, FAM), (FAM, RM)])
def test_mode_jump_rejected(start, target):
    """跳级（RM→FAM / FAM→RM）拒绝，抛 ValueError。"""
    mgr = ebm.EmergencyBrakeManager(mode=start)
    with pytest.raises(ValueError):
        mgr.set_mode(target)


def test_ato_fault_degrades_fam_to_cm():
    """ATO 故障：FAM 下紧急制动并自动降级 CM。"""
    mgr = ebm.EmergencyBrakeManager(mode=FAM)
    result = mgr.trigger("ato_fault")
    assert result["applied"] is True
    assert mgr.state == ebm.STATE_BRAKE
    assert mgr.mode == CM  # 故障驱动的安全降级


def test_atp_fault_degrades_to_rm():
    """ATP 故障：FAM/CM 下紧急制动并自动降级 RM。"""
    for start in (FAM, CM):
        mgr = ebm.EmergencyBrakeManager(mode=start)
        result = mgr.trigger("atp_fault")
        assert result["applied"] is True
        assert result["mode_change"] == RM
        assert mgr.mode == RM


# ---- 5. SIL 双通道表决 ----


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("overspeed", True),  # SIL4：需要双通道验证
        ("atp_fault", True),
        ("ato_fault", False),  # SIL2
        ("fire_alarm", False),
    ],
)
def test_safety_verification_sil4_only(reason, expected):
    """SIL4 原因要求双通道一致验证，SIL2 不需要。"""
    mgr = ebm.EmergencyBrakeManager()
    assert mgr.safety_verification(reason) is expected


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize("a,b", [(True, False), (False, True), (True, True)])
def test_sil4_channel_any_triggers(a, b):
    """SIL4 紧急制动：任一通道触发即制动（故障安全，宁可错杀不可漏放）。"""
    mgr = ebm.EmergencyBrakeManager()
    assert mgr.channel_vote("overspeed", a, b) is True


@pytest.mark.smoke
@pytest.mark.safety
@pytest.mark.parametrize(
    "a,b,expected",
    [
        (True, False, False),  # 单通道触发：不制动（防误报）
        (False, True, False),
        (False, False, False),
        (True, True, True),  # 双通道一致：制动
    ],
)
def test_sil2_channel_both_required(a, b, expected):
    """SIL2 原因：双通道一致才制动（防误报）。"""
    mgr = ebm.EmergencyBrakeManager()
    assert mgr.channel_vote("fire_alarm", a, b) is expected


# ---- 6. 非法输入 ----


def test_unknown_mode_raises():
    mgr = ebm.EmergencyBrakeManager()
    with pytest.raises(ValueError):
        mgr.set_mode("AUTO")


def test_unknown_mode_at_init_raises():
    with pytest.raises(ValueError):
        ebm.EmergencyBrakeManager(mode="AUTO")


@pytest.mark.parametrize("reason", ["ghost_reason", "", "overspeed "])
def test_unknown_reason_raises(reason):
    """未知原因在 trigger/update/safety/vote 中均抛 ValueError。"""
    mgr = ebm.EmergencyBrakeManager()
    with pytest.raises(ValueError):
        mgr.trigger(reason)
    with pytest.raises(ValueError):
        mgr.update_reason_status(reason, True)
    with pytest.raises(ValueError):
        mgr.safety_verification(reason)
    with pytest.raises(ValueError):
        mgr.channel_vote(reason, True, False)


# ---- 7. 记录与审计 ----


def test_inapplicable_reason_recorded_not_braking():
    """非法组合（RM + 门开）：记录提示但不误制动，记录可审计。"""
    mgr = ebm.EmergencyBrakeManager(mode=RM)
    result = mgr.trigger("door_open")
    assert result["applied"] is False
    assert mgr.state == ebm.STATE_IDLE
    assert len(mgr.records) == 1
    assert mgr.records[0]["action"] == "record_only"
    assert mgr.records[0]["mode"] == RM
    assert mgr.records[0]["reason"] == "door_open"


# ---- 8. 缓解/复位安全前提（速度有效性，对标真实 EBR 缓解条件） ----


def test_release_denied_when_speed_signal_invalid():
    """速度传感器失效（speed_valid=False）：速度按 0 喂入也不得缓解。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    assert mgr.state == ebm.STATE_BRAKE
    assert mgr.release_condition(0.0, speed_valid=False) is False
    assert mgr.state == ebm.STATE_BRAKE  # 不得迁移 RELEASED


def test_release_allowed_when_speed_valid_at_zero():
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    assert mgr.release_condition(0.0, speed_valid=True) is True
    assert mgr.state == ebm.STATE_RELEASED


def test_self_heal_denied_while_moving():
    """运行中自愈被拒绝：紧急制动不得在运行中自动解除。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    assert mgr.self_heal(speed_kmh=80.0) is False
    assert mgr.state == ebm.STATE_BRAKE
    assert mgr.self_heal(speed_kmh=0.0, speed_valid=False) is False
    assert mgr.state == ebm.STATE_BRAKE


def test_self_heal_allowed_at_zero_speed():
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    assert mgr.self_heal(speed_kmh=0.0) is True
    assert mgr.state == ebm.STATE_IDLE


def test_reset_denied_while_moving():
    """运行中远程复位被拒绝：等于运行中解除紧急制动，必须报错。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    with pytest.raises(ValueError):
        mgr.reset(speed_kmh=80.0)
    assert mgr.state == ebm.STATE_BRAKE
    with pytest.raises(ValueError):
        mgr.reset(speed_kmh=0.0, speed_valid=False)
    assert mgr.state == ebm.STATE_BRAKE


def test_reset_allowed_at_zero_with_cleared_reasons():
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    mgr.reset(speed_kmh=0.0)
    assert mgr.state == ebm.STATE_IDLE
    assert mgr.self_heal_used == 0  # 自愈额度恢复


# ---- 9. SIL2 表决通道失效诊断 ----


def test_sil2_mismatch_counts_diagnostic():
    """SIL2 表决：双通道不一致 → 不制动 + 累计通道失效诊断计数。"""
    mgr = ebm.EmergencyBrakeManager()
    assert mgr.channel_vote("fire_alarm", True, False) is False
    assert mgr.channel_vote("fire_alarm", False, True) is False
    assert mgr.channel_vote("fire_alarm", True, True) is True  # 一致才制动
    assert mgr.vote_mismatches == 2  # 两次不一致被诊断


def test_sil4_mismatch_not_counted():
    """SIL4 表决：任一触发即制动（不一致属正常容错），不计诊断。"""
    mgr = ebm.EmergencyBrakeManager()
    assert mgr.channel_vote("overspeed", True, False) is True
    assert mgr.channel_vote("overspeed", False, True) is True
    assert mgr.vote_mismatches == 0


# ---- 10. 司机缓解操作序列（手柄回零 + 缓解按钮保持） ----


def test_driver_release_sequence_success():
    """完整司机缓解闭环：BRAKE→手柄回零→按钮保持≥3s→IDLE。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    assert mgr.prepare_release(0, speed_kmh=0.0) is True
    assert mgr.state == ebm.STATE_WAIT_HANDLE_ZERO
    assert mgr.hold_release_button(ebm.RELEASE_HOLD_S) is True
    assert mgr.state == ebm.STATE_IDLE


def test_driver_release_requires_handle_zero():
    """手柄未回零 → 拒绝进入缓解序列（真实操作第一步就是回零）。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    assert mgr.prepare_release(5, speed_kmh=0.0) is False
    assert mgr.state == ebm.STATE_BRAKE
    assert mgr.prepare_release(-1, speed_kmh=0.0) is False


def test_driver_release_requires_reasons_cleared():
    """原因仍在 → 序列禁止启动（不消原因不停车不缓解）。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", True)
    assert mgr.prepare_release(0, speed_kmh=0.0) is False
    assert mgr.state == ebm.STATE_BRAKE


def test_driver_release_requires_zero_speed():
    """运行中手柄回零也不能启动缓解（速度校验）。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    assert mgr.prepare_release(0, speed_kmh=40.0) is False
    assert mgr.prepare_release(0, speed_kmh=0.0, speed_valid=False) is False
    assert mgr.state == ebm.STATE_BRAKE


def test_driver_release_requires_brake_state():
    """IDLE 状态直接操作序列无效。"""
    mgr = ebm.EmergencyBrakeManager()
    assert mgr.prepare_release(0) is False
    assert mgr.hold_release_button(5.0) is False
    assert mgr.state == ebm.STATE_IDLE


def test_hold_button_short_then_retry():
    """按钮保持不足 → WAIT_RELEASE_BTN 可重试；再次保持足够 → IDLE。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    mgr.prepare_release(0, speed_kmh=0.0)
    assert mgr.hold_release_button(1.0) is False  # 1s < 3s
    assert mgr.state == ebm.STATE_WAIT_RELEASE_BTN
    assert mgr.hold_release_button(3.0) is True  # 重试成功
    assert mgr.state == ebm.STATE_IDLE


def test_hold_button_boundary_exact_hold():
    """边界：恰好 RELEASE_HOLD_S 秒 = 成功。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    mgr.prepare_release(0, speed_kmh=0.0)
    assert mgr.hold_release_button(ebm.RELEASE_HOLD_S) is True
    # 略低于阈值
    mgr2 = ebm.EmergencyBrakeManager()
    mgr2.trigger("overspeed")
    mgr2.update_reason_status("overspeed", False)
    mgr2.prepare_release(0, speed_kmh=0.0)
    assert mgr2.hold_release_button(ebm.RELEASE_HOLD_S - 0.001) is False


def test_release_sequence_vs_auto_release_paths_coexist():
    """司机序列与自动缓解（release_condition→RELEASED→reset）互不干扰。"""
    mgr = ebm.EmergencyBrakeManager()
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    # 自动路径：直接 RELEASED
    assert mgr.release_condition(0.0) is True
    assert mgr.state == ebm.STATE_RELEASED
    # RELEASED 状态不可再走司机序列（已非 BRAKE）
    assert mgr.prepare_release(0) is False
