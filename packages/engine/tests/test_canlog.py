"""CAN 日志解析/回放测试（canlog.py）：.asc 解析、回放、统计。"""

import pytest

from tcms import canlog as cl

SAMPLE_ASC = """date Wed Jan 15 10:30:00 2026
base hex  timestamps absolute
internal events logged
Begin Triggerblock Wed Jan 15 10:30:00.000 2026
    0.000000  1  100   Rx   d 8 11 22 33 44 55 66 77 88
    0.010000  2  200   Tx   d 4 00 00 00 00
    0.020000  1  0x2A0 Rx   d 2 AA BB
    0.030000  1  300   Rx   d 0
End Triggerblock
"""


# ---- 解析 ----


def test_parse_asc_basic():
    frames = cl.parse_asc(SAMPLE_ASC)
    assert len(frames) == 4
    assert frames[0]["arb_id"] == 0x100
    assert frames[0]["direction"] == cl.DIRECTION_RX
    assert frames[0]["data"] == bytes.fromhex("11 22 33 44 55 66 77 88")
    assert frames[1]["arb_id"] == 0x200
    assert frames[1]["direction"] == cl.DIRECTION_TX
    assert frames[2]["arb_id"] == 0x2A0  # hex ID 支持
    assert frames[3]["data"] == b""


def test_parse_asc_sorted_by_ts():
    """乱序输入 → 按时间升序输出。"""
    text = "    0.500000  1  100   Rx   d 1 01\n    0.100000  1  100   Rx   d 1 02\n"
    frames = cl.parse_asc(text)
    assert [f["ts"] for f in frames] == [0.1, 0.5]


def test_parse_asc_ignores_non_frame_lines():
    text = "garbage line\n    1.000000  1  100   Rx   d 1 01\nEnd Triggerblock\n"
    frames = cl.parse_asc(text)
    assert len(frames) == 1


def test_parse_asc_skips_bad_dlc_mismatch():
    """DLC 与数据长度不符 → 跳过。"""
    text = "    0.000000  1  100   Rx   d 8 01 02\n"
    assert cl.parse_asc(text) == []


def test_parse_asc_skips_bad_hex():
    text = "    0.000000  1  ZZ   Rx   d 1 01\n"
    assert cl.parse_asc(text) == []


def test_parse_asc_file(tmp_path):
    p = tmp_path / "log.asc"
    p.write_text(SAMPLE_ASC, encoding="utf-8")
    frames = cl.parse_asc_file(str(p))
    assert len(frames) == 4


# ---- 回放 ----


def test_replayer_run_calls_callback_all_frames():
    frames = cl.parse_asc(SAMPLE_ASC)
    seen = []
    replayer = cl.AscReplayer(frames, speed=10.0)
    n = replayer.run_fast(on_frame=seen.append)
    assert n == 4
    assert len(seen) == 4
    assert replayer.played_count == 4


def test_replayer_speed_validation():
    with pytest.raises(ValueError):
        cl.AscReplayer([], speed=0)


def test_replayer_run_sleeps_between_frames(monkeypatch):
    """run() 按时间间隔 sleep（真实时间基准）。"""
    frames = cl.parse_asc(
        "    0.000000  1  100   Rx   d 1 01\n    0.100000  1  100   Rx   d 1 02\n"
    )
    slept = []
    import time

    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    replayer = cl.AscReplayer(frames, speed=2.0)  # 0.1s → 0.05s
    replayer.run(on_frame=lambda f: None)
    assert len(slept) == 1
    assert slept[0] == pytest.approx(0.05)


# ---- 统计 ----


def test_log_stats_counts_and_distribution():
    frames = cl.parse_asc(SAMPLE_ASC)
    stats = cl.log_stats(frames)
    assert stats["frames"] == 4
    assert stats["duration_s"] == pytest.approx(0.03)
    assert stats["by_id"] == {"0x100": 1, "0x200": 1, "0x2a0": 1, "0x300": 1}


def test_log_stats_empty():
    stats = cl.log_stats([])
    assert stats["frames"] == 0
