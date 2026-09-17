"""CAN 错误状态机测试：计数规则、状态迁移、Bus-Off 恢复、损坏帧统计、回调、非法输入。

对标 ISO 11898-1 错误处理规则逐条验证：
    - TEC/REC 增减规则（+8 / -1 / 被动跳变 120/119）
    - 状态判据（<128 active、>=128 passive、TEC>=256 bus-off）
    - Bus-Off 后 128 次总线空闲恢复
"""

import pytest

from tcms.errstate import (
    BUS_IDLE_RECOVERY,
    ERROR_PASSIVE_MIN,
    ERROR_TYPES,
    RX_SUCCESS_PASSIVE_TARGET,
    STATE_BUS_OFF,
    STATE_ERROR_ACTIVE,
    STATE_ERROR_PASSIVE,
    SUSPEND_TRANSMIT_BITS,
    TX_SUCCESS_PASSIVE_TARGET,
    CanErrorStateMachine,
)

# ---- 初态与基础计数规则 ----


def test_initial_state():
    m = CanErrorStateMachine()
    assert m.state == STATE_ERROR_ACTIVE
    assert m.tec == 0
    assert m.rec == 0
    assert m.error_frames == 0
    assert all(v == 0 for v in m.error_counts.values())


@pytest.mark.parametrize("kind", ERROR_TYPES)
def test_tx_error_counts_per_type(kind):
    m = CanErrorStateMachine()
    m.tx_error(kind)
    assert m.tec == 8
    assert m.error_counts[kind] == 1
    assert m.error_frames == 1
    assert m.rec == 0  # 发送错误不影响接收计数


def test_rx_error_increments_rec_only():
    m = CanErrorStateMachine()
    m.rx_error("crc_error")
    assert m.rec == 8
    assert m.tec == 0
    assert m.error_frames == 1


def test_tx_error_8_times_stays_active():
    """15 次发送错误 TEC=120，仍 Error-Active。"""
    m = CanErrorStateMachine()
    for _ in range(15):
        m.tx_error()
    assert m.tec == 120
    assert m.state == STATE_ERROR_ACTIVE


# ---- 状态迁移判据 ----


def test_tx_errors_cross_passive_threshold():
    """TEC 达到 128 转入 Error-Passive（16 次发送错误）。"""
    m = CanErrorStateMachine()
    for _ in range(16):
        m.tx_error()
    assert m.tec == ERROR_PASSIVE_MIN
    assert m.state == STATE_ERROR_PASSIVE


def test_rx_errors_cross_passive_threshold():
    """REC 达到 128 同样转入 Error-Passive。"""
    m = CanErrorStateMachine()
    for _ in range(16):
        m.rx_error()
    assert m.rec == ERROR_PASSIVE_MIN
    assert m.state == STATE_ERROR_PASSIVE


def test_counter_capped_at_255():
    """计数器 8 位封顶 255，不再越界。"""
    m = CanErrorStateMachine()
    for _ in range(40):
        m.tx_error()
    assert m.tec == 255
    assert m.state in (STATE_ERROR_PASSIVE, STATE_BUS_OFF)


# ---- Bus-Off 触发与离线 ----


@pytest.mark.smoke
@pytest.mark.safety
def test_tec_reaches_bus_off_threshold():
    """TEC 越过 256 触发 Bus-Off（仅发送错误路径）。"""
    m = CanErrorStateMachine()
    for _ in range(31):
        m.tx_error()
    assert m.tec == 248
    assert m.state == STATE_ERROR_PASSIVE
    m.tx_error()  # 248 + 8 = 256 → Bus-Off
    assert m.state == STATE_BUS_OFF
    assert m.tec == 255  # 封顶存储


@pytest.mark.smoke
@pytest.mark.safety
def test_bus_off_isolates_node():
    """Bus-Off 期间节点离线：错误注入与成功事件全部 no-op，不累计。"""
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    assert m.state == STATE_BUS_OFF
    frames = m.error_frames
    m.tx_error()
    m.rx_error()
    m.tx_success()
    m.rx_success()
    assert m.tec == 255
    assert m.rec == 0
    assert m.error_frames == frames  # 离线节点感知不到总线错误


@pytest.mark.safety
def test_bus_off_recovery_after_128_idle_bits():
    """Bus-Off 恢复：累计 128 次总线空闲后复位 Error-Active，计数归零。"""
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    assert m.state == STATE_BUS_OFF
    for _ in range(BUS_IDLE_RECOVERY - 1):
        m.bus_idle_bit()
    assert m.state == STATE_BUS_OFF  # 还差 1 次
    m.bus_idle_bit()
    assert m.state == STATE_ERROR_ACTIVE
    assert m.tec == 0
    assert m.rec == 0
    assert m.bus_idle == 0


def test_bus_idle_bit_noop_when_not_bus_off():
    """非 Bus-Off 状态下 bus_idle_bit 是 no-op（幂等）。"""
    m = CanErrorStateMachine()
    assert m.bus_idle_bit(5) == STATE_ERROR_ACTIVE
    assert m.state == STATE_ERROR_ACTIVE


# ---- 成功事件递减规则 ----


def test_tx_success_decrements_tec():
    m = CanErrorStateMachine()
    for _ in range(2):
        m.tx_error()
    assert m.tec == 16
    m.tx_success()
    assert m.tec == 15
    m.tx_success()
    m.tx_success()
    assert m.tec == 13  # 16 → 15 → 14 → 13


def test_tx_success_at_zero_stays_zero():
    m = CanErrorStateMachine()
    m.tx_success()
    assert m.tec == 0
    assert m.state == STATE_ERROR_ACTIVE


def test_rx_success_decrements_rec():
    m = CanErrorStateMachine()
    for _ in range(3):
        m.rx_error()
    assert m.rec == 24
    m.rx_success()
    assert m.rec == 23


def test_passive_tx_success_jumps_to_120():
    """TEC>=128 成功发送后直接置 120（被动快速回归，ISO 11898-1）。"""
    m = CanErrorStateMachine()
    for _ in range(16):
        m.tx_error()
    assert m.state == STATE_ERROR_PASSIVE
    m.tx_success()
    assert m.tec == TX_SUCCESS_PASSIVE_TARGET  # 120 < 128 → 恢复 Error-Active
    assert m.state == STATE_ERROR_ACTIVE


def test_passive_rx_success_jumps_to_119():
    """REC>=128 成功接收后直接置 119（被动快速回归）。"""
    m = CanErrorStateMachine()
    for _ in range(16):
        m.rx_error()
    assert m.state == STATE_ERROR_PASSIVE
    m.rx_success()
    assert m.rec == RX_SUCCESS_PASSIVE_TARGET  # 119 < 128 → 恢复 Error-Active
    assert m.state == STATE_ERROR_ACTIVE


def test_mixed_errors_and_success_oscillation():
    """混合注入：错误/成功交替，状态与计数始终一致。"""
    m = CanErrorStateMachine()
    for _ in range(10):
        m.tx_error()
        m.rx_error()
    assert m.tec == 80 and m.rec == 80
    assert m.state == STATE_ERROR_ACTIVE
    for _ in range(20):
        m.tx_error()
    assert m.tec == 240  # 80 + 20*8
    assert m.state == STATE_ERROR_PASSIVE  # 240 < 256，尚未 Bus-Off
    m.tx_error()  # 248
    assert m.state == STATE_ERROR_PASSIVE
    m.tx_error()  # 256 → Bus-Off
    assert m.state == STATE_BUS_OFF


# ---- 错误类型与统计 ----


def test_error_counts_track_all_types():
    m = CanErrorStateMachine()
    m.tx_error("bit_error")
    m.tx_error("stuff_error")
    m.rx_error("crc_error")
    m.rx_error("form_error")
    m.tx_error("ack_error")
    assert m.error_counts == {
        "bit_error": 1,
        "stuff_error": 1,
        "crc_error": 1,
        "form_error": 1,
        "ack_error": 1,
    }
    assert m.error_frames == 5


@pytest.mark.parametrize("kind", ["unknown", "", "crc", "bit"])
def test_unknown_error_type_raises(kind):
    m = CanErrorStateMachine()
    with pytest.raises(ValueError):
        m.tx_error(kind)
    with pytest.raises(ValueError):
        m.rx_error(kind)


def test_error_counter_relationship():
    """计数-状态关系在任意注入后保持一致（辅助验收断言）。"""
    m = CanErrorStateMachine()
    for _ in range(20):
        m.rx_error()
    assert m.rec == 160
    assert m.state == STATE_ERROR_PASSIVE
    assert m.tec == 0  # 未超过 256，无 Bus-Off 风险


# ---- 状态迁移回调 ----


def test_state_change_listener():
    """on_state_change 回调收到完整迁移序列 active→passive→bus-off→active。"""
    seen = []
    m = CanErrorStateMachine(on_state_change=seen.append)
    assert seen == []  # 初态不回调
    for _ in range(32):
        m.tx_error()
    assert seen == [STATE_ERROR_PASSIVE, STATE_BUS_OFF]
    for _ in range(BUS_IDLE_RECOVERY):
        m.bus_idle_bit()
    assert seen[-1] == STATE_ERROR_ACTIVE
    assert seen == [STATE_ERROR_PASSIVE, STATE_BUS_OFF, STATE_ERROR_ACTIVE]


# ---- 复位 ----


def test_reset_recovers_immediately():
    """软件复位：清除计数并立即恢复 Error-Active（诊断/测试路径）。"""
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    assert m.state == STATE_BUS_OFF
    m.reset()
    assert m.state == STATE_ERROR_ACTIVE
    assert m.tec == 0 and m.rec == 0
    # 复位后错误统计保留（历史审计）
    assert m.error_frames == 32


def test_recovery_path_then_real_traffic_resumes():
    """Bus-Off → 128 次空闲恢复 → 正常收发不再计数变化。"""
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    m.bus_idle_bit(BUS_IDLE_RECOVERY)
    assert m.state == STATE_ERROR_ACTIVE
    for _ in range(10):
        m.tx_success()
        m.rx_success()
    assert m.tec == 0 and m.rec == 0
    assert m.state == STATE_ERROR_ACTIVE


# ---- Bus-Off 恢复后发送退避（suspend transmission） ----


def test_recovery_sets_suspend_backoff():
    """Bus-Off → 128 空闲恢复后，发送前需 8 位退避（ISO 11898-1）。"""
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    assert m.state == STATE_BUS_OFF
    m.bus_idle_bit(BUS_IDLE_RECOVERY)
    assert m.state == STATE_ERROR_ACTIVE
    assert m.tx_backoff_remaining() == SUSPEND_TRANSMIT_BITS  # 恢复后 8 位退避


def test_backoff_decrements_and_floor_zero():
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    m.bus_idle_bit(BUS_IDLE_RECOVERY)
    m.tx_backoff_bit(3)
    assert m.tx_backoff_remaining() == 5
    m.tx_backoff_bit(10)
    assert m.tx_backoff_remaining() == 0  # 退避完成
    m.tx_backoff_bit(5)
    assert m.tx_backoff_remaining() == 0  # 不跌为负


def test_no_backoff_without_bus_off():
    """从未 Bus-Off 的节点无退避（初始 0）。"""
    m = CanErrorStateMachine()
    assert m.tx_backoff_remaining() == 0


def test_reset_clears_backoff():
    m = CanErrorStateMachine()
    for _ in range(32):
        m.tx_error()
    m.bus_idle_bit(BUS_IDLE_RECOVERY)
    assert m.tx_backoff_remaining() == SUSPEND_TRANSMIT_BITS
    m.reset()
    assert m.tx_backoff_remaining() == 0
