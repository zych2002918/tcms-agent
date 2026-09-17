"""EB 执行反馈闭环测试：压力反馈 / 回执 / 牵引联锁 / 超时 / 时间单调性。"""

import pytest

from tcms import exec_feedback as ef

# ---- 构造参数校验 ----


def test_default_state_idle():
    mon = ef.EbExecutionFeedback()
    assert mon.state == ef.STATE_IDLE
    assert mon.pending_request is None
    assert mon.records == []


def test_invalid_timeout_rejected():
    with pytest.raises(ValueError):
        ef.EbExecutionFeedback(timeout_s=0)
    with pytest.raises(ValueError):
        ef.EbExecutionFeedback(timeout_s=-1.0)


def test_invalid_pressure_thresholds_rejected():
    # 释放阈值必须小于施加阈值，否则状态机无意义
    with pytest.raises(ValueError):
        ef.EbExecutionFeedback(applied_kpa=100.0, released_kpa=100.0)
    with pytest.raises(ValueError):
        ef.EbExecutionFeedback(applied_kpa=100.0, released_kpa=200.0)


# ---- 请求 ----


def test_request_eb_enters_pending():
    mon = ef.EbExecutionFeedback()
    assert mon.request_eb("overspeed", ts=10.0) is True
    assert mon.state == ef.STATE_PENDING
    req = mon.pending_request
    assert req["reason"] == "overspeed"
    assert req["ts"] == 10.0


def test_new_request_overwrites_pending():
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=1.0)
    mon.request_eb("door_open", ts=2.0)
    assert mon.pending_request["reason"] == "door_open"


# ---- 成功闭环（三重证据齐备 → APPLIED） ----


def test_full_confirm_success():
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    # 顺序：压力 → 回执 → 牵引切除（全部齐备才 APPLIED）
    assert mon.state == ef.STATE_PENDING
    mon.on_pressure(350.0, ts=0.1)
    assert mon.state == ef.STATE_PENDING  # 单条证据不构成确认
    mon.on_eb_active(True, ts=0.2)
    assert mon.state == ef.STATE_PENDING
    mon.on_traction(False, ts=0.3)
    assert mon.state == ef.STATE_APPLIED  # 三重证据齐备


def test_confirm_order_independent():
    """证据到达顺序不影响确认结果（反馈消息乱序是常态）。"""
    mon = ef.EbExecutionFeedback()
    mon.request_eb("atp_fault", ts=0.0)
    mon.on_traction(False, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    assert mon.state == ef.STATE_PENDING
    mon.on_pressure(400.0, ts=0.3)
    assert mon.state == ef.STATE_APPLIED


def test_pressure_below_threshold_not_confirmed():
    mon = ef.EbExecutionFeedback(applied_kpa=300.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.on_eb_active(True, ts=0.1)
    mon.on_traction(False, ts=0.2)
    mon.on_pressure(299.9, ts=0.3)  # 差一点未达标
    assert mon.state == ef.STATE_PENDING
    mon.on_pressure(300.0, ts=0.4)  # 恰好达标
    assert mon.state == ef.STATE_APPLIED


# ---- 超时（执行层失效） ----


def test_timeout_without_any_feedback():
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    assert mon.evaluate(ts=2.0) is True  # 恰好时限内
    assert mon.evaluate(ts=2.001) is False  # 超过时限 → FAULT
    assert mon.state == ef.STATE_FEEDBACK_FAULT


def test_timeout_partial_feedback_reports_missing():
    """压力到位但回执/牵引切除缺失 → 超时报告缺失项明细。"""
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(320.0, ts=0.5)  # 只有压力证据
    assert mon.evaluate(ts=2.5) is False
    assert mon.state == ef.STATE_FEEDBACK_FAULT
    fault = next(r for r in mon.records if r["event"] == "fault")
    assert set(fault["missing"]) == {"eb_active", "traction_off"}


def test_pressure_feedback_late_after_timeout():
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=2.5)  # 超时后的压力反馈
    assert mon.state == ef.STATE_FEEDBACK_FAULT


# ---- 牵引联锁（最危险的执行层失效） ----


@pytest.mark.smoke
@pytest.mark.safety
def test_interlock_violation_immediate_fault():
    """APPLIED 期间牵引恢复 → 不等超时，立即 FAULT。"""
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(False, ts=0.3)
    assert mon.state == ef.STATE_APPLIED
    mon.on_traction(True, ts=0.4)  # 边制动边牵引
    assert mon.state == ef.STATE_FEEDBACK_FAULT
    fault = next(r for r in mon.records if r["event"] == "fault")
    assert fault["cause"] == "interlock_violation"


def test_traction_active_during_pending_blocks_confirm():
    """PENDING 期间牵引未切除：不能确认，超时兜底。"""
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(True, ts=0.3)  # 牵引仍激活
    assert mon.state == ef.STATE_PENDING
    assert mon.evaluate(ts=2.1) is False
    assert mon.state == ef.STATE_FEEDBACK_FAULT


# ---- 缓解反馈闭环 ----


def test_release_pressure_falls_back_to_idle():
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(False, ts=0.3)
    assert mon.state == ef.STATE_APPLIED
    mon.on_pressure(49.0, ts=1.0)  # 压力回落 → 缓解完成
    assert mon.state == ef.STATE_IDLE
    assert mon.pending_request is None


def test_release_pressure_still_high_stays_applied():
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(False, ts=0.3)
    mon.on_pressure(60.0, ts=1.0)  # > 50 kPa：尚未缓解
    assert mon.state == ef.STATE_APPLIED


def test_eb_active_cleared_without_pressure_no_release():
    """回执撤除但压力未回落：不判缓解（压力才是缓解的物证）。"""
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(False, ts=0.3)
    mon.on_eb_active(False, ts=1.0)
    assert mon.state == ef.STATE_APPLIED  # 等待压力回落证据


# ---- 复位 ----


def test_reset_from_fault():
    mon = ef.EbExecutionFeedback(timeout_s=1.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.evaluate(ts=1.5)
    assert mon.state == ef.STATE_FEEDBACK_FAULT
    mon.reset()
    assert mon.state == ef.STATE_IDLE
    assert mon.pending_request is None
    # 审计记录保留
    assert [r["event"] for r in mon.records][-1] == "reset"


# ---- 时间单调性（乱序/重放防护） ----


def test_rejects_out_of_order_timestamp():
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=10.0)
    mon.on_pressure(350.0, ts=10.1)
    assert mon.on_pressure(300.0, ts=10.0) is False  # 时间倒流样本
    assert [r["event"] for r in mon.records][-1] == "rejected_timestamp"
    # 状态未被倒流样本破坏
    assert mon.state == ef.STATE_PENDING


def test_request_with_out_of_order_timestamp_rejected():
    mon = ef.EbExecutionFeedback()
    mon.request_eb("overspeed", ts=5.0)
    assert mon.request_eb("door_open", ts=4.0) is False
    assert mon.pending_request["reason"] == "overspeed"
    assert mon.state == ef.STATE_PENDING


# ---- evaluate 健康路径 ----


def test_evaluate_healthy_when_confirmed():
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(False, ts=0.3)
    assert mon.state == ef.STATE_APPLIED
    assert mon.evaluate(ts=5.0) is True  # APPLIED 不受超时窗口影响
    assert mon.state == ef.STATE_APPLIED


def test_evaluate_healthy_within_timeout():
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    assert mon.evaluate(ts=1.9) is True
    assert mon.state == ef.STATE_PENDING


# ---- 审计记录 ----


def test_records_audit_trail_complete():
    mon = ef.EbExecutionFeedback(timeout_s=2.0)
    mon.request_eb("overspeed", ts=0.0)
    mon.on_pressure(350.0, ts=0.1)
    mon.on_eb_active(True, ts=0.2)
    mon.on_traction(False, ts=0.3)
    mon.on_pressure(30.0, ts=1.0)
    events = [r["event"] for r in mon.records]
    assert events == [
        "request",
        "pressure_reached",
        "eb_active_ack",
        "traction_off",
        "applied",
        "released",
    ]
