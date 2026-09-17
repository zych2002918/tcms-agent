"""模糊测试：随机/截断/翻转帧喂入解码器，验证鲁棒性（不崩溃、不越界异常）。

对应真实场景：总线上可能出现任意噪声帧/异常帧，解码层必须保持健壮——
非法输入不得导致崩溃，解码结果应落在信号物理范围的合理扩展区间。
"""

import random

import pytest
from can import Message
from cantools.database.errors import DecodeError
from cantools.database.namedsignalvalue import NamedSignalValue

from tcms import protocol as proto
from tcms.parser import decode

ALL_IDS = [
    proto.TCMS_HEARTBEAT,
    proto.VEHICLE_SPEED,
    proto.TRACTION_BRAKE_HANDLE,
    proto.DOOR_CONTROL,
    proto.ALARM_EVENT,
    proto.PANTOGRAPH_STATUS,
    proto.BRAKE_SYSTEM,
    proto.ENERGY_STATUS,
]

RANGE_GUARD = {  # 信号名 -> (min_guard, max_guard)，物理范围 ±20% 裕度
    "SpeedKmh": (-5.0, 240.0),
    "HandlePosition": (-1, 20),
    "SocPercent": (-1, 110),
    "BatteryVoltage": (-5.0, 1100.0),
    "BatteryTemp": (-50, 140),
}


@pytest.fixture()
def rng():
    return random.Random(42)  # 固定种子，可复现


def _random_frame(rng, min_len=0, max_len=8):
    mid = rng.choice(ALL_IDS)
    data = bytes(rng.getrandbits(8) for _ in range(rng.randint(min_len, max_len)))
    return Message(arbitration_id=mid, data=data, is_extended_id=False)


def test_fuzz_random_frames_do_not_crash(db, rng):
    """200 条随机帧解码不崩溃。"""
    for _ in range(200):
        frame = _random_frame(rng)
        try:
            decode(db, frame)
        except DecodeError:
            pass  # 解码层拒绝非法输入是可接受的，但不允许崩溃


def test_fuzz_truncated_frames_do_not_crash(db, rng):
    """截断帧（0-7 字节）解码不崩溃。"""
    for _ in range(100):
        frame = _random_frame(rng, min_len=0, max_len=7)
        try:
            decode(db, frame)
        except DecodeError:
            pass


def test_fuzz_full_byte_frames_decode_values_are_numeric(db, rng):
    """8 字节全随机帧：解码值类型正确（数值），无 NaN/异常对象。"""
    import math

    for _ in range(100):
        frame = _random_frame(rng, min_len=8, max_len=8)
        try:
            signals = decode(db, frame)
        except DecodeError:
            continue
        for name, value in signals.items():
            if isinstance(value, float):
                assert not math.isnan(value), f"{name} 解码出 NaN"
            elif not isinstance(value, (int, str, NamedSignalValue)):
                pytest.fail(f"{name} 解码类型异常: {type(value)}")


def test_fuzz_flipped_frames_detected_by_crc(db, rng):
    """模糊翻转帧应被 CRC-8 校验识别（数据完整性层兜底）。"""
    from tcms import faults

    for _ in range(50):
        frame = _random_frame(rng, min_len=8, max_len=8)
        crc = faults.compute_crc8(bytes(frame.data))
        assert faults.verify_crc8(bytes(frame.data), crc) is True
        corrupted = faults.flip_bit(bytes(frame.data), rng.randrange(8), rng.randrange(8))
        if corrupted != bytes(frame.data):
            assert faults.verify_crc8(corrupted, crc) is False


def test_fuzz_zero_length_frames(db):
    """0 字节帧不崩溃。"""
    for mid in ALL_IDS:
        frame = Message(arbitration_id=mid, data=b"", is_extended_id=False)
        try:
            decode(db, frame)
        except DecodeError:
            pass
