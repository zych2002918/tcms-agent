"""EBR 紧急制动硬线回路测试：串联触点、fail-safe 失电制动、断线诊断、双回路 2oo2。

验证"紧急制动执行路径独立于 CAN"的现实建模：
    - 任一常闭触点开路 → 失电 → 制动（fail-safe）
    - 断线 → 制动 + 可诊断区分（请求源闭合仍失电 = 断线）
    - 双回路任一失电即制动，单条断线仅降级不损失制动能力
"""

import pytest

from tcms.ebr import (
    DIAG_OK,
    DIAG_OPEN_REQUEST,
    DIAG_WIRE_BREAK,
    LOOP_DEENERGIZED,
    LOOP_ENERGIZED,
    EbrLoop,
    EbrLoopPair,
)


@pytest.fixture()
def loop():
    return EbrLoop(contacts=("driver_handle", "atp_contact", "emergency_btn"))


# ---- 初态与触点操作 ----


def test_initial_energized(loop):
    assert loop.energized is True
    assert loop.state == LOOP_ENERGIZED
    assert loop.brake_applied is False
    assert loop.open_contacts == ()
    assert loop.diag_pulse() == DIAG_OK


def test_open_contact_deenergizes_and_applies_brake(loop):
    loop.open_contact("emergency_btn")
    assert loop.energized is False
    assert loop.state == LOOP_DEENERGIZED
    assert loop.brake_applied is True  # 失电即制动
    assert loop.open_contacts == ("emergency_btn",)


def test_any_contact_open_breaks_loop(loop):
    """串联回路：任一触点开路即失电（多个触点逐一验证）。"""
    for name in ("driver_handle", "atp_contact", "emergency_btn"):
        for opened in loop.open_contacts:
            loop.close_contact(opened)  # 复位上一个触点的开路
        assert loop.energized
        loop.open_contact(name)
        assert not loop.energized


def test_close_contact_reenergizes(loop):
    loop.open_contact("atp_contact")
    loop.close_contact("atp_contact")
    assert loop.energized is True
    assert loop.brake_applied is False


def test_multiple_open_contacts_listed(loop):
    loop.open_contact("driver_handle")
    loop.open_contact("emergency_btn")
    assert loop.open_contacts == ("driver_handle", "emergency_btn")


def test_unknown_contact_raises(loop):
    with pytest.raises(ValueError):
        loop.open_contact("nope")
    with pytest.raises(ValueError):
        loop.close_contact("nope")


# ---- 断线故障 ----


def test_wire_break_deenergizes(loop):
    """断线 = 失电 = 制动（fail-safe：物理故障方向即制动方向）。"""
    loop.break_wire()
    assert loop.wire_broken is True
    assert loop.energized is False
    assert loop.brake_applied is True


def test_wire_break_diagnosed_distinct_from_request(loop):
    """断线与触点开路都是失电，但诊断可区分（请求源闭合仍失电=断线）。"""
    loop.break_wire()
    assert loop.diag_pulse() == DIAG_WIRE_BREAK
    assert loop.diagnose_wire_break() is True
    loop.repair_wire()
    loop.open_contact("driver_handle")
    assert loop.diag_pulse() == DIAG_OPEN_REQUEST
    assert loop.diagnose_wire_break() is False


def test_repair_wire_restores(loop):
    loop.break_wire()
    loop.repair_wire()
    assert loop.energized is True
    assert loop.wire_broken is False


def test_wire_break_plus_open_contact(loop):
    """断线 + 触点开路同时存在：修复断线后仍因请求开路而失电。"""
    loop.break_wire()
    loop.open_contact("atp_contact")
    assert loop.diag_pulse() == DIAG_WIRE_BREAK  # 断线优先报告
    loop.repair_wire()
    assert loop.diag_pulse() == DIAG_OPEN_REQUEST
    assert not loop.energized


# ---- 双回路 2oo2 ----


@pytest.fixture()
def pair():
    return EbrLoopPair(EbrLoop(name="EBR-A"), EbrLoop(name="EBR-B"))


def test_pair_initial_healthy(pair):
    assert pair.brake_applied is False
    h = pair.health()
    assert h["brake_applied"] is False
    assert h["degraded"] is False
    assert h["loop_a"]["state"] == LOOP_ENERGIZED
    assert h["loop_b"]["state"] == LOOP_ENERGIZED


def test_pair_any_loop_open_applies_brake(pair):
    """2oo2 fail-safe：任一回路失电即制动（防单回路故障漏制动）。"""
    pair.loop_a.open_contact("emergency_btn")
    assert pair.brake_applied is True
    pair.loop_a.close_contact("emergency_btn")
    pair.loop_b.open_contact("driver_handle")
    assert pair.brake_applied is True


def test_pair_single_wire_break_degrades_but_brakes(pair):
    """单条断线：该回路失电 → 制动施加（不损失制动能力），另一回路健康 → 降级预警。"""
    pair.loop_a.break_wire()
    assert pair.brake_applied is True  # 断线方向 = 制动方向
    assert pair.degraded is True  # 双回路不一致 = 降级
    h = pair.health()
    assert h["degraded"] is True
    assert h["loop_a"]["diag"] == DIAG_WIRE_BREAK
    assert h["loop_b"]["diag"] == DIAG_OK


def test_pair_both_breaks(pair):
    pair.loop_a.break_wire()
    pair.loop_b.break_wire()
    assert pair.brake_applied is True
    assert pair.degraded is False  # 双回路同状态（都断）不算降级，是双故障


def test_pair_repair(pair):
    pair.loop_a.break_wire()
    pair.loop_b.break_wire()
    pair.repair()
    assert pair.brake_applied is False
    assert pair.degraded is False
    assert pair.loop_a.energized and pair.loop_b.energized


def test_pair_requires_distinct_loops():
    a = EbrLoop()
    with pytest.raises(ValueError):
        EbrLoopPair(a, a)


def test_pair_open_contact_not_repaired_by_repair(pair):
    """repair() 只修物理断线，不清除制动请求（触点开路须请求源解除）。"""
    pair.loop_a.open_contact("emergency_btn")
    pair.repair()
    assert pair.brake_applied is True  # 请求仍在 → 仍制动


# ---- 集成：EBM 硬线备份路径（hardwire_loss 的物理执行建模） ----


def test_hardwire_loss_path_deenergizes_loop():
    """CAN 网络故障（hardwire_loss）→ EBR 回路失电制动 = 硬线备份路径。"""
    from tcms.ebm import EmergencyBrakeManager

    mgr = EmergencyBrakeManager()
    loop = EbrLoop()
    # CAN 侧：EBM 判定网络丢失 → 紧急制动
    result = mgr.trigger("hardwire_loss")
    assert result["applied"] is True
    # 物理侧：EBR 回路因紧急制动请求开路 → 失电 → 制动
    loop.open_contact("atp_contact")
    assert loop.brake_applied is True
    assert mgr.state == "BRAKE"


def test_full_ebr_brake_release_cycle():
    """EBR 闭环：请求 → 失电制动 → 请求解除 → 得电缓解。"""
    loop = EbrLoop()
    loop.open_contact("driver_handle")  # 司机手柄推到 EB 位
    assert loop.brake_applied is True
    loop.close_contact("driver_handle")  # 手柄回缓解位
    assert loop.brake_applied is False
    assert loop.state == LOOP_ENERGIZED
