"""完整回放链测试（replay.py）：.asc → 业务逻辑 → 证据断言。

验证 ReplayEngine / ReplayChain 的虚拟时钟驱动回放、业务评估
（联锁/ATP/看门狗/EBM）、告警与事件记录器联动。
"""

import pytest

from tcms import atp, ebm, replay, watchdogs
from tcms.canlog import parse_asc


def _speed_frame(ts: float, speed_kmh: float, speed_valid: bool = True) -> str:
    raw = int(round(speed_kmh * 10.0))
    valid = 1 if speed_valid else 0
    return f"    {ts:.3f}  1  200   Rx   d 3 {raw & 0xFF:02X} {(raw >> 8) & 0xFF:02X} {valid:02X}"


def _door_frame(ts: float, door_states=(0, 0, 0, 0)) -> str:
    b0 = (
        (door_states[0] & 0x03)
        | ((door_states[1] & 0x03) << 2)
        | ((door_states[2] & 0x03) << 4)
        | ((door_states[3] & 0x03) << 6)
    )
    all_closed = 1 if all(s == 0 for s in door_states) else 0
    return f"    {ts:.3f}  1  400   Rx   d 2 {b0:02X} {(all_closed | 0x02):02X}"


def _heartbeat_frame(ts: float) -> str:
    return f"    {ts:.3f}  1  100   Rx   d 1 01"


# ---- 基础回放 ----


def test_replay_chain_from_text_parses_frames():
    text = "base hex timestamps absolute\n" + _speed_frame(0.1, 60.0)
    chain = replay.ReplayChain.from_text(text)
    assert len(chain.frames) == 1
    report = chain.run()
    assert report["frames"] == 1
    assert report["ebm_triggered"] is False


def test_replay_chain_run_no_alerts_on_normal_drive():
    """正常行驶（60km/h、门关、心跳正常）不触发任何告警。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _heartbeat_frame(0.000),
            _heartbeat_frame(0.100),  # 连续 2 次心跳 → online
            _door_frame(0.110),
            _speed_frame(0.120, 60.0),
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["alerts"] == []
    assert report["ebm_state"] == ebm.STATE_IDLE
    assert report["watchdog_states"] == {"vcu": watchdogs.STATE_ONLINE}


# ---- 超速 → EBM ----


def test_replay_overspeed_triggers_ebm():
    """速度超过 EBI（>160km/h）→ EBM 触发 + 告警。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _door_frame(0.010),
            _speed_frame(0.020, 60.0),
            _speed_frame(0.030, 170.0),  # > EBI(160)
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["ebm_triggered"] is True
    assert report["ebm_state"] == ebm.STATE_BRAKE
    kinds = {a["kind"] for a in report["alerts"]}
    assert replay.ALERT_EBM_TRIGGER in kinds
    assert replay.ALERT_ATP_LEVEL in kinds


def test_replay_overspeed_uses_atp_ebi_threshold():
    """150km/h（>warning 155? 否）不触发；160.5 触发 EBI。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _door_frame(0.010),
            _speed_frame(0.020, 150.0),
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["ebm_triggered"] is False
    # 160.5 > EBI(160) 触发
    text2 = "base hex timestamps absolute\n" + "\n".join(
        [
            _door_frame(0.010),
            _speed_frame(0.020, 160.5),
        ]
    )
    report2 = replay.ReplayChain.from_text(text2).run()
    assert report2["ebm_triggered"] is True


# ---- 门开冲突 → EBM ----


def test_replay_door_open_while_moving_triggers_ebm():
    """运行中车门打开 → 联锁冲突 + EBM。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _door_frame(0.010, door_states=(1, 0, 0, 0)),  # 门1开
            _speed_frame(0.020, 60.0),
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["ebm_triggered"] is True
    details = {a["detail"] for a in report["alerts"] if a["kind"] == replay.ALERT_OVERRIDE_OVERRUN}
    assert "door_open_while_moving" in details


def test_replay_door_open_at_zero_speed_no_trigger():
    """零速开门不触发（门-车联锁只在移动时生效）。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _door_frame(0.010, door_states=(1, 0, 0, 0)),
            _speed_frame(0.020, 0.0),
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["ebm_triggered"] is False


# ---- 看门狗离线 ----


@pytest.mark.safety
def test_replay_watchdog_fault_on_missing_heartbeat():
    """心跳丢失超过 miss_threshold 周期 → 看门狗 fault 告警。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _heartbeat_frame(0.000),
            _heartbeat_frame(0.100),
            _heartbeat_frame(0.200),
            # 之后无心跳，但仍有速度帧推进时间
            _speed_frame(0.500, 60.0),
            _speed_frame(1.000, 60.0),
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["watchdog_states"] == {"vcu": watchdogs.STATE_FAULT}
    kinds = {a["kind"] for a in report["alerts"]}
    assert replay.ALERT_WATCHDOG_FAULT in kinds


def test_replay_watchdog_online_with_regular_heartbeat():
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _heartbeat_frame(0.000),
            _heartbeat_frame(0.100),
            _heartbeat_frame(0.200),
            _heartbeat_frame(0.300),
        ]
    )
    report = replay.ReplayChain.from_text(text).run()
    assert report["watchdog_states"] == {"vcu": watchdogs.STATE_ONLINE}


# ---- 事件记录器联动 ----


def test_replay_records_events_in_recorder():
    """回放过程全部事件进入 recorder，可导出/查询。"""
    text = "base hex timestamps absolute\n" + "\n".join(
        [
            _heartbeat_frame(0.000),
            _speed_frame(0.020, 170.0),
        ]
    )
    chain = replay.ReplayChain.from_text(text)
    chain.run()
    events = list(chain.engine.rec)
    # 帧事件 + 告警事件
    assert any(e["type"] == "can_rx" for e in events)
    assert any(e["category"] == "replay" for e in events)
    assert any(e["message"] and "ebm_trigger" in e["message"] for e in events)


def test_replay_report_after_run():
    """run() 返回的报告结构完整。"""
    text = "base hex timestamps absolute\n" + _speed_frame(0.1, 60.0)
    report = replay.ReplayChain.from_text(text).run()
    for key in (
        "frames",
        "alerts",
        "alert_kinds",
        "ebm_state",
        "ebm_triggered",
        "watchdog_states",
        "atp_last_level",
    ):
        assert key in report


# ---- ReplayEngine 直用 ----


def test_engine_accepts_frames_and_on_frame_callback():
    frames = parse_asc("base hex timestamps absolute\n" + _speed_frame(0.1, 60.0))
    seen = []
    engine = replay.ReplayEngine(frames)
    n = engine.run(on_frame=seen.append)
    assert n == 1
    assert len(seen) == 1
    assert engine.now == pytest.approx(0.1)


def test_engine_with_custom_objects():
    """可注入自定义 EBM/ATP/看门狗（测试扩展性）。"""
    mgr = ebm.EmergencyBrakeManager()
    sup = atp.SpeedSupervisor(limit_kmh=100)
    wd = watchdogs.NodeHealthTable(now=lambda: 0.0)
    frames = parse_asc(
        "base hex timestamps absolute\n" + _heartbeat_frame(0.0) + "\n" + _speed_frame(0.1, 120.0)
    )
    engine = replay.ReplayEngine(frames, ebm_manager=mgr, atp_supervisor=sup, watchdog_table=wd)
    engine.run()
    assert mgr.state == ebm.STATE_BRAKE  # 120 > EBI(100) 触发
