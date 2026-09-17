#!/usr/bin/env python3
"""生成 examples/demo_trip.asc —— 一趟含故障的模拟列车运行 CAN 日志。

剧情（时间轴，全部帧通道 1、hex ID）:
    t=0.00–4.00  列车加速至 120 km/h 巡航；VCU 心跳 0x100 每 100ms；
                 车速 0x200 每 100ms（little-endian 0.1km/h 步进）
    t=2.00–2.40  VCU 心跳中断 400ms → 看门狗连续丢 4 个周期 → vcu fault
    t=2.40       心跳恢复
    t=5.00       车速 185 km/h > EBI 160 → ATP ebi 监督 + EBM 触发(overspeed)
    t=5.60       速度回 0（零速）→ EBM 缓解
    t=7.00       车门误开（门1=打开）且车速 60 km/h → 门-车联锁违规
                 + EBM 触发(door_open)
    t=8.00       车门关闭、速度归零 → 全部恢复，日志结束

帧数据字节布局（与 replay.ReplayEngine 解析一致）:
    0x200  VehicleSpeed: data[0:2]=速度 raw (0.1km/h LE)，data[2] bit0=valid
    0x400  DoorControl:  data[0] 每 2bit 一门（01=打开 00=关闭 10=故障）
    0x100  TCMS_Heartbeat: data 全 0x00（仅节律相关）
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "demo_trip.asc"

HEARTBEAT = 0x100
VEHICLE_SPEED = 0x200
DOOR_CONTROL = 0x400


def _hb(ts: float) -> str:
    return f"{ts:9.6f}  1  {HEARTBEAT:x}   Rx   d 8 00 00 00 00 00 00 00 00"


def _speed(ts: float, kmh: float, valid: bool = True) -> str:
    raw = int(kmh * 10)
    b0 = raw & 0xFF
    b1 = (raw >> 8) & 0xFF
    v = 1 if valid else 0
    return f"{ts:9.6f}  1  {VEHICLE_SPEED:x}   Rx   d 8 {b0:02x} {b1:02x} {v:02x} 00 00 00 00 00"


def _door(ts: float, *states: int) -> str:
    """states: 每门 00/01/10（关闭/打开/故障），最多 4 门。"""
    byte = 0
    for i, s in enumerate(states):
        byte |= (s & 0x03) << (2 * i)
    return f"{ts:9.6f}  1  {DOOR_CONTROL:x}   Rx   d 8 {byte:02x} 00 00 00 00 00 00 00"


def _speed_ramp(start_ts: float, kmh_from: float, kmh_to: float, step: float, step_s: float):
    """从 start_ts 起按 step_s 间隔从 kmh_from 爬升到 kmh_to，产出帧行。"""
    lines: list[tuple[float, str]] = []
    kmh = kmh_from
    ts = start_ts
    while kmh <= kmh_to:
        lines.append((ts, _speed(ts, kmh)))
        ts += step_s
        kmh += step
    return lines


def main() -> None:
    lines: list[tuple[float, str]] = []
    t = 0.0

    # 心跳：0–2.0s 每 100ms
    while t < 2.0:
        lines.append((t, _hb(t)))
        t += 0.1
    # 心跳中断 2.0–2.4（不产帧即模拟中断）
    # 心跳恢复 2.4–8.0
    t = 2.4
    while t < 8.0:
        lines.append((t, _hb(t)))
        t += 0.1

    # 车速：0–4s 加速 0→120（每 0.2s +10），巡航到 8s
    lines += _speed_ramp(0.0, 0, 120, 10, 0.2)
    t = 4.2
    while t < 5.0:
        lines.append((t, _speed(t, 120)))
        t += 0.2
    # 超速段 5.0–5.6：185 km/h
    t = 5.0
    while t < 5.6:
        lines.append((t, _speed(t, 185)))
        t += 0.2
    # 紧急制动减速归零 5.6–6.6（EBM 缓解条件：零速）
    lines += _speed_ramp(5.6, 185, 0, -30, 0.2)
    t = 6.6
    while t < 7.0:
        lines.append((t, _speed(t, 0)))
        t += 0.2
    # 缓解后重新加速 7.0–8.0 → 60 km/h（移动中触发门剧情）
    lines += _speed_ramp(7.0, 0, 60, 10, 0.2)

    # 门剧情：0–7s 全关；7.0–8.0 门1打开
    t = 0.0
    while t < 7.0:
        lines.append((t, _door(t, 0, 0, 0, 0)))
        t += 0.2
    t = 7.0
    while t < 8.0:
        lines.append((t, _door(t, 1, 0, 0, 0)))
        t += 0.2

    lines.sort(key=lambda x: x[0])
    header = [
        "date So 2026-09-02 09:30:00",
        "base hex  timestamps absolute",
        "internal events logged",
        "Begin Triggerblock So 2026-09-02 09:30:00.000000",
        "",
    ]
    body = [ln for _, ln in lines]
    footer = ["", "End Triggerblock"]

    OUT.write_text("\n".join(header + body + footer) + "\n", encoding="utf-8")
    print(f"已生成 {OUT}（{len(body)} 帧）")


if __name__ == "__main__":
    main()
