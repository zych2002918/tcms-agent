"""报文序列/时序违规检测测试：乱序/重复/迟到/丢帧。"""

from tcms import seqcheck as sc

MISSING = sc.VIOLATION_MISSING
DUPLICATE = sc.VIOLATION_DUPLICATE
OUT_OF_ORDER = sc.VIOLATION_OUT_OF_ORDER
LATE = sc.VIOLATION_LATE


# ---- 正常流 ----


def test_clean_sequence_no_violations():
    ck = sc.SequenceChecker(period_s=0.1)
    for i in range(10):
        ck.on_frame(0x100, i, i * 0.1)
    assert ck.violations == {MISSING: 0, DUPLICATE: 0, OUT_OF_ORDER: 0, LATE: 0}
    assert ck.last_violation is None


# ---- 乱序 ----


def test_out_of_order_detected():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 7, 0.1)  # 跳过 6
    assert ck.violations[OUT_OF_ORDER] == 1
    assert ck.last_violation == OUT_OF_ORDER


def test_seq_wraparound_ok():
    ck = sc.SequenceChecker(period_s=0.1, seq_mod=256)
    ck.on_frame(0x100, 255, 0.0)
    ck.on_frame(0x100, 0, 0.1)  # 回绕：合法
    assert ck.violations[OUT_OF_ORDER] == 0


def test_seq_reversal_detected():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 4, 0.1)  # 回退
    assert ck.violations[OUT_OF_ORDER] == 1


# ---- 重复 ----


def test_duplicate_same_seq_short_interval():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 5, 0.02)  # 同序号 + 短间隔：重复
    assert ck.violations[DUPLICATE] == 1


def test_duplicate_same_seq_next_cycle_ok():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 6, 0.1)
    ck.on_frame(0x100, 5, 0.2)  # 回绕式复用（间隔 0.1）：不算重复
    # 注意：5 是 6 的下一个合法序号，间隔 0.1 也正常 → 无重复
    assert ck.violations[DUPLICATE] == 0


# ---- 迟到 ----


def test_late_frame_detected():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 6, 0.15)  # 0.15 > 0.12：迟到
    assert ck.violations[LATE] == 1


def test_late_tolerance_boundary():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 6, 0.12)  # 恰好上限：不迟到
    assert ck.violations[LATE] == 0


# ---- 丢帧（超时） ----


def test_timeout_missing_detected():
    ck = sc.SequenceChecker(period_s=0.1, timeout_s=0.3)
    ck.on_frame(0x100, 5, 0.0)
    ck.check_timeout(0x100, 0.35)  # > 0.3：丢帧
    assert ck.violations[MISSING] == 1


def test_timeout_within_ok():
    ck = sc.SequenceChecker(period_s=0.1, timeout_s=0.3)
    ck.on_frame(0x100, 5, 0.0)
    ck.check_timeout(0x100, 0.2)  # < 0.3：未丢
    assert ck.violations[MISSING] == 0


# ---- 多 ID 隔离 ----


def test_multiple_ids_isolated():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x200, 5, 0.0)  # 不同 ID 各自独立序列
    ck.on_frame(0x200, 7, 0.1)  # 0x200 乱序，不影响 0x100
    assert ck.violations[OUT_OF_ORDER] == 1
    assert ck.total_frames(0x100) == 1
    assert ck.total_frames(0x200) == 2
    assert ck.total_frames() == 3


# ---- 重置 ----


def test_reset_clears_state():
    ck = sc.SequenceChecker(period_s=0.1)
    ck.on_frame(0x100, 5, 0.0)
    ck.on_frame(0x100, 9, 0.1)  # 乱序
    ck.reset()
    assert ck.violations[OUT_OF_ORDER] == 0
    assert ck.last_violation is None
    assert ck.total_frames() == 0
