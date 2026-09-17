"""错误注入与数据完整性校验（测试视角的健壮性验证）。

对应真实总线场景中的"噪声/干扰/位翻转"：CAN 物理层本身有硬件 CRC，
但车载应用层（如 J1939 传输、诊断报文）通常还有软件级完整性校验。
本模块提供：

- 位翻转 / 字节损坏注入（模拟电磁干扰、总线位错误）
- CRC-8 应用层校验（CRC-8/ATM，多项式 0x07）
- 帧完整性校验：识别被破坏的报文
"""

CRC8_ATM_TABLE = None


def _build_table() -> list[int]:
    global CRC8_ATM_TABLE
    if CRC8_ATM_TABLE is not None:
        return CRC8_ATM_TABLE
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    CRC8_ATM_TABLE = table
    return table


def compute_crc8(data: bytes) -> int:
    """CRC-8/ATM 校验（多项式 0x07，初值 0）。参考向量: b'123456789' -> 0xF4。"""
    crc = 0
    table = _build_table()
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF]
    return crc


def verify_crc8(data: bytes, expected_crc: int) -> bool:
    """校验数据完整性。"""
    return compute_crc8(data) == (expected_crc & 0xFF)


def flip_bit(data: bytes, byte_idx: int, bit_idx: int) -> bytes:
    """翻转指定字节的指定位（模拟单比特翻转噪声）。"""
    if not 0 <= byte_idx < len(data) or not 0 <= bit_idx < 8:
        raise ValueError(f"越界: byte={byte_idx}, bit={bit_idx}, len={len(data)}")
    b = bytearray(data)
    b[byte_idx] ^= 1 << bit_idx
    return bytes(b)


def corrupt_byte(data: bytes, byte_idx: int, mask: int = 0xFF) -> bytes:
    """按掩码损坏指定字节（模拟总线干扰）。"""
    if not 0 <= byte_idx < len(data):
        raise ValueError(f"越界: byte={byte_idx}, len={len(data)}")
    b = bytearray(data)
    b[byte_idx] ^= mask & 0xFF
    return bytes(b)


def corrupt_frame(msg, byte_idx: int, mask: int = 0xFF):
    """注入损坏：返回数据被破坏的新报文对象（报文头信息不变）。"""
    new_data = corrupt_byte(bytes(msg.data), byte_idx, mask)
    msg2 = type(msg)(
        arbitration_id=msg.arbitration_id,
        data=new_data,
        is_extended_id=msg.is_extended_id,
    )
    return msg2
