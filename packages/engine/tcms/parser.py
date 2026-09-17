"""CAN 报文解析与校验辅助。

- decode(): cantools 解码原始报文
- 周期/丢报检测辅助
"""

import time

from can import Bus, Message


def decode(db, msg: Message) -> dict:
    """解码一条 CAN 报文为信号字典。"""
    return db.decode_message(msg.arbitration_id, msg.data)


def collect(bus: Bus, duration: float, expect_ids: set[int], db) -> dict[int, list[dict]]:
    """在 duration 秒内采集总线报文，按报文 ID 分组返回解码结果。"""
    end = time.monotonic() + duration
    collected: dict[int, list[dict]] = {mid: [] for mid in expect_ids}
    while time.monotonic() < end:
        msg = bus.recv(timeout=0.2)
        if msg is None:
            continue
        if msg.arbitration_id in expect_ids:
            collected[msg.arbitration_id].append(decode(db, msg))
    return collected


def count_frames(bus: Bus, duration: float, message_id: int) -> int:
    """统计指定报文在 duration 秒内的帧数（用于周期验证）。"""
    end = time.monotonic() + duration
    count = 0
    while time.monotonic() < end:
        msg = bus.recv(timeout=0.2)
        if msg is not None and msg.arbitration_id == message_id:
            count += 1
    return count
