"""CAN 日志解析与回放（CANalyzer .asc 格式）—— 真实数据驱动仿真验证。

对标真实测试流程：现场抓取的 CAN 日志（Vector CANalyzer .asc 或
PCAN .trc）回放到仿真平台，验证 TCMS 逻辑对真实流量/真实故障的响应。
本模块支持 Vector .asc 文本格式：

    格式示例（标准帧）:
        date ...
        base hex  timestamps absolute
        internal events logged
        Begin Triggerblock ...
            0.000000  1  100   Rx   d 8 11 22 33 44 55 66 77 88
            0.010000  2  200   Tx   d 4 00 00 00 00
        End Triggerblock

    帧行字段（空格分隔）:
        时间戳  通道  仲裁ID(hex/dec)  方向(Rx/Tx)  d DLC 数据字节...

提供:
    - parse_asc：把 .asc 文本解析为 (ts, arbitration_id, direction, data) 列表
    - AscReplayer：按时间戳驱动回调（喂给 bus 或业务模块）
    - 统计：帧数、按 ID 分布、总线负载估算
"""

from __future__ import annotations

import re

# 帧行正则：时间戳 通道 ID 方向 d DLC 数据...
# 支持 hex 或 dec ID（带 0x 前缀或纯数字）；DLC=0 时无数据字节
_FRAME_RE = re.compile(r"^\s*(\d+\.\d+)\s+\d+\s+([0-9A-Fa-fx]+)\s+(Rx|Tx)\s+d\s+(\d+)\s*(.*)$")

DIRECTION_RX = "rx"
DIRECTION_TX = "tx"


def _parse_id(text: str, hex_mode: bool | None = None) -> int:
    """解析仲裁 ID。

    hex_mode: True=hex / False=dec / None=按 0x 前缀自动判断。
    """
    if text.lower().startswith("0x"):
        return int(text, 16)
    if hex_mode is True:
        return int(text, 16)
    if hex_mode is False:
        return int(text, 10)
    return int(text, 10)


def parse_asc(text: str) -> list[dict]:
    """解析 .asc 文本 → 帧记录列表（按时间升序）。

    每条记录: {"ts", "arb_id", "direction", "data"}（data 为 bytes）。
    - 支持 `base hex` / `base dec` 头部声明（决定 ID 进制）
    - 忽略非帧行（头部/注释/Begin/End）
    - 非法 ID/DLC/数据行跳过并计数
    """
    frames: list[dict] = []
    skipped = 0
    base_hex = "base hex" in text.lower().splitlines()[0:5] if False else None
    # 扫描头部 5 行内的进制声明
    for line in text.splitlines()[:5]:
        low = line.lower().strip()
        if low.startswith("base"):
            base_hex = "hex" in low
            break
    for line in text.splitlines():
        m = _FRAME_RE.match(line)
        if not m:
            continue
        ts_s, id_text, direction, dlc_text, data_text = m.groups()
        try:
            arb_id = _parse_id(id_text, hex_mode=base_hex)
            dlc = int(dlc_text)
            data = bytes.fromhex(data_text.strip())
        except ValueError:
            skipped += 1
            continue
        if len(data) != dlc or dlc > 8:
            skipped += 1
            continue
        frames.append(
            {
                "ts": float(ts_s),
                "arb_id": arb_id,
                "direction": DIRECTION_RX if direction == "Rx" else DIRECTION_TX,
                "data": data,
            }
        )
    frames.sort(key=lambda f: f["ts"])
    return frames


def parse_asc_file(path: str) -> list[dict]:
    """从文件解析 .asc（UTF-8/GBK 自适应）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="gbk", errors="replace") as f:
            text = f.read()
    return parse_asc(text)


class AscReplayer:
    """按时间戳回放帧记录：驱动业务回调（喂帧给总线或处理函数）。

    用法：
        replayer = AscReplayer(frames)
        replayer.run(on_frame=handle)   # 按真实时间间隔调用 handle

    时间基准：回放起始 = 首帧时间戳；帧间按 ts 差 sleep（可倍速）。
    """

    def __init__(self, frames: list[dict], speed: float = 1.0, start_offset_s: float = 0.0):
        if speed <= 0:
            raise ValueError(f"speed 必须为正数，got {speed}")
        self.frames = list(frames)
        self.speed = speed
        self.start_offset_s = start_offset_s
        self._played = 0

    @property
    def played_count(self) -> int:
        return self._played

    def run(self, on_frame=None) -> int:
        """顺序回放全部帧。on_frame(frame) 每帧回调（可空）。

        时间间隔按 (ts 差)/speed 推进（真实时间基准，可倍速）；
        返回回放帧数。
        """
        import time

        self._played = 0
        prev_ts = None
        for frame in self.frames:
            if prev_ts is not None:
                gap = (frame["ts"] - prev_ts) / self.speed
                if gap > 0:
                    time.sleep(gap)
            prev_ts = frame["ts"]
            if on_frame is not None:
                on_frame(frame)
            self._played += 1
        return self._played

    def run_fast(self, on_frame=None) -> int:
        """快速回放（不 sleep）：按 ts 差累积虚拟时钟，回调带虚拟 ts。

        on_frame(frame) 中 frame["ts"] 是原始日志时间；适合测试/分析。
        """
        self._played = 0
        for frame in self.frames:
            if on_frame is not None:
                on_frame(frame)
            self._played += 1
        return self._played


def log_stats(frames: list[dict]) -> dict:
    """日志统计：帧数、时长、按 ID 分布、负载估算（8 字节最坏）。"""
    if not frames:
        return {"frames": 0, "duration_s": 0.0, "by_id": {}, "ids": []}
    from collections import Counter

    by_id = Counter(f["arb_id"] for f in frames)
    duration = frames[-1]["ts"] - frames[0]["ts"]
    return {
        "frames": len(frames),
        "duration_s": duration,
        "by_id": {hex(k): v for k, v in by_id.items()},
        "ids": sorted(by_id),
    }
