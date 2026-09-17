"""错误注入与数据完整性测试：CRC 校验、位翻转、字节损坏、损坏帧识别。"""

import pytest
from can import Message

from tcms import faults
from tcms import protocol as proto

# CRC-8/ATM 标准参考向量：b"123456789" 的校验值为 0xF4
CRC8_REF_VECTOR = 0xF4


@pytest.mark.smoke
def test_crc8_reference_vector():
    """CRC-8/ATM 应通过标准参考向量（123456789 -> 0xF4）。"""
    assert faults.compute_crc8(b"123456789") == CRC8_REF_VECTOR


def test_crc8_empty_data():
    assert faults.compute_crc8(b"") == 0


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"\x00", 0x00),
        (b"\x01", 0x07),
        (b"\x00\x00", 0x00),
        (b"\xff", 0xF3),
    ],
)
def test_crc8_known_values(data, expected):
    assert faults.compute_crc8(data) == expected


def test_verify_crc8_ok():
    data = b"\x01\x02\x03\x04"
    crc = faults.compute_crc8(data)
    assert faults.verify_crc8(data, crc) is True


def test_verify_crc8_detects_corruption():
    data = b"\x01\x02\x03\x04"
    crc = faults.compute_crc8(data)
    corrupted = faults.flip_bit(data, 2, 3)
    assert corrupted != data
    assert faults.verify_crc8(corrupted, crc) is False


def test_flip_bit_single_bit():
    """翻转 1 位应只改变 1 个字节的 1 个位。"""
    data = b"\x00\x00\x00"
    out = faults.flip_bit(data, 1, 4)
    assert out == b"\x00\x10\x00"


def test_flip_bit_out_of_range():
    with pytest.raises(ValueError):
        faults.flip_bit(b"\x00", 5, 0)
    with pytest.raises(ValueError):
        faults.flip_bit(b"\x00", 0, 9)


def test_corrupt_byte_mask():
    data = b"\xff\xff"
    assert faults.corrupt_byte(data, 0, 0x0F) == b"\xf0\xff"


def test_corrupt_byte_out_of_range():
    with pytest.raises(ValueError):
        faults.corrupt_byte(b"\x00", 3)


def test_corrupt_frame_preserves_id():
    """损坏帧注入：报文 ID/扩展标志不变，仅数据被破坏。"""
    msg = Message(arbitration_id=proto.VEHICLE_SPEED, data=b"\x01\x02\x03", is_extended_id=False)
    bad = faults.corrupt_frame(msg, 0, 0xFF)
    assert bad.arbitration_id == msg.arbitration_id
    assert bad.is_extended_id == msg.is_extended_id
    assert bad.data != msg.data


def test_flipped_speed_frame_decodes_differently(db):
    """位翻转注入后，车速报文解码值应发生变化（数据完整性受损）。"""
    from tcms import protocol as proto
    from tcms.parser import decode

    raw = proto.encode(db, "VehicleSpeed", SpeedKmh=100.0, SpeedValid=1, SpeedSource=1)
    good = Message(arbitration_id=proto.VEHICLE_SPEED, data=raw, is_extended_id=False)
    speed_good = decode(db, good)["SpeedKmh"]

    flipped = faults.flip_bit(bytes(raw), 0, 7)  # 车速高字节最高位翻转
    bad = Message(arbitration_id=proto.VEHICLE_SPEED, data=flipped, is_extended_id=False)
    speed_bad = decode(db, bad)["SpeedKmh"]
    assert speed_good == 100.0
    assert speed_bad != speed_good
    # 翻转后应能被 CRC 校验识别
    assert faults.verify_crc8(bytes(raw), faults.compute_crc8(bytes(raw)))
    assert not faults.verify_crc8(flipped, faults.compute_crc8(bytes(raw)))
