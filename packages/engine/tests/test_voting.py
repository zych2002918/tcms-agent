"""2oo3 速度表决器测试：多数一致/单通道故障/表决失效/边界。"""

import pytest

from tcms import voting as vt

CH_A, CH_B, CH_C = vt.CH_A, vt.CH_B, vt.CH_C
VOTE_VALID, VOTE_DIVERGENT, VOTE_FAILED = vt.VOTE_VALID, vt.VOTE_DIVERGENT, vt.VOTE_FAILED


# ---- 基本表决 ----


@pytest.mark.parametrize(
    "speeds,expected_valid,expected_speed",
    [
        ([80.0, 80.0, 80.0], True, 80.0),  # 三通道一致
        ([80.0, 81.0, 80.0], True, 80.5),  # 两两一致（容差内）取均值
        ([80.0, 82.0, 80.0], True, 81.0),  # 容差 2.0：80/82 匹配 → 均值 81
        ([80.0, 83.0, 86.0], False, 0.0),  # 两两差 >2：无一致组，发散
        ([80.0, 200.0, 200.0], True, 200.0),  # 两通道一致（80 为噪声）
        ([80.0, 81.0, 200.0], True, 80.5),  # 80/81 一致，200 为故障读数
    ],
)
def test_vote_basic(speeds, expected_valid, expected_speed):
    ok, speed, state = SpeedVoter().vote(speeds)
    assert ok is expected_valid
    if expected_valid:
        assert speed == expected_speed
        assert state == VOTE_VALID
    else:
        assert state == VOTE_DIVERGENT


def SpeedVoter():
    return vt.SpeedVoter2oo3()


def test_vote_divergent_state():
    ok, _, state = SpeedVoter().vote([10.0, 50.0, 90.0])
    assert not ok
    assert state == VOTE_DIVERGENT


# ---- 通道故障 ----


def test_mark_faulty_channel_ignored():
    voter = SpeedVoter()
    voter.mark_faulty(CH_B)
    ok, speed, state = voter.vote([80.0, 999.0, 80.0])  # B 故障读数被忽略
    assert ok and speed == 80.0 and state == VOTE_VALID


def test_two_faulty_channels_failed():
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    voter.mark_faulty(CH_B)
    ok, _, state = voter.vote([80.0, 81.0, 80.0])
    assert not ok
    assert state == VOTE_FAILED


def test_clear_fault_restores():
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    assert CH_A in voter.faulty_channels
    voter.clear_fault(CH_A)
    assert not voter.faulty_channels
    ok, speed, _ = voter.vote([80.0, 80.0, 80.0])
    assert ok and speed == 80.0


def test_invalid_channel_raises():
    voter = SpeedVoter()
    with pytest.raises(ValueError):
        voter.mark_faulty(5)
    with pytest.raises(ValueError):
        voter.clear_fault(-1)


# ---- 边界与异常 ----


def test_vote_requires_three_readings():
    with pytest.raises(ValueError):
        SpeedVoter().vote([80.0, 81.0])


def test_custom_tolerance():
    voter = vt.SpeedVoter2oo3(tolerance_kmh=5.0)
    ok, speed, _ = voter.vote([80.0, 84.0, 80.0])  # 差 4 < 容差 5：80/84 匹配取均值
    assert ok and speed == 82.0


def test_faulty_channels_property_is_copy():
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    voter.faulty_channels.add(CH_B)  # 修改副本不影响内部
    assert voter.faulty_channels == {CH_A}


# ---- 2oo3 → 2oo2 降级路径（容错演进） ----


def test_single_fault_degrades_to_2oo2():
    """单通道故障 → 架构 2oo3 → 2oo2，仍可正常表决（容错演进）。"""
    voter = SpeedVoter()
    assert voter.architecture == "2oo3"
    assert not voter.degraded
    voter.mark_faulty(CH_A)
    assert voter.architecture == "2oo2"
    assert voter.degraded
    assert voter.healthy_channels == [CH_B, CH_C]
    # 2oo2 下两健康通道一致 → 有效
    ok, speed, state = voter.vote([999.0, 80.0, 80.0])
    assert ok and speed == 80.0 and state == VOTE_VALID


def test_2oo2_requires_agreement():
    """2oo2 降级后：两通道不一致 → 发散（比 2oo3 更严格）。"""
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    ok, _, state = voter.vote([999.0, 80.0, 83.0])  # 差 3 > 容差 2
    assert not ok and state == VOTE_DIVERGENT


def test_2oo2_marginal_agreement():
    """2oo2 降级后：容差边缘（差=2.0）仍一致。"""
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    ok, speed, _ = voter.vote([999.0, 80.0, 82.0])
    assert ok and speed == 81.0


def test_degrade_events_counted_on_mark_and_clear():
    """降级/恢复各计一次事件。"""
    voter = SpeedVoter()
    assert voter.degrade_events == 0
    voter.mark_faulty(CH_A)  # 2oo3 → 2oo2
    assert voter.degrade_events == 1
    voter.mark_faulty(CH_A)  # 重复标记：不重复计
    assert voter.degrade_events == 1
    voter.clear_fault(CH_A)  # 2oo2 → 2oo3 恢复
    assert voter.degrade_events == 2
    assert voter.architecture == "2oo3"


def test_second_fault_fails_voter():
    """第二通道故障 → 健康通道 <2 → 表决器失效（failed）。"""
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    voter.mark_faulty(CH_B)
    assert voter.architecture == "failed"
    ok, _, state = voter.vote([80.0, 81.0, 80.0])
    assert not ok and state == VOTE_FAILED


def test_2oo2_disagreement_does_not_use_majority():
    """2oo2 下两通道各执一词（差超容差）→ 不可采信，即使有"多数"也无意义。"""
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    ok, _, state = voter.vote([999.0, 80.0, 100.0])
    assert not ok and state == VOTE_DIVERGENT


def test_recover_after_clear_fault():
    """清除故障恢复 2oo3：表决回到三通道多数一致。"""
    voter = SpeedVoter()
    voter.mark_faulty(CH_A)
    assert voter.architecture == "2oo2"
    voter.clear_fault(CH_A)
    assert voter.architecture == "2oo3"
    ok, speed, _ = voter.vote([80.0, 200.0, 200.0])  # 200/200 多数一致
    assert ok and speed == 200.0
