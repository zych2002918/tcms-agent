"""事件记录器测试：环形缓冲、查询过滤、统计、导出、总线装饰器、EBM/errstate 接线。

验证 Recorder 作为"统一时间线"的完整行为，以及与其他模块的互操作。
"""

import json
from types import SimpleNamespace

import pytest

from tcms.ebm import EmergencyBrakeManager
from tcms.errstate import STATE_ERROR_PASSIVE, CanErrorStateMachine
from tcms.recorder import (
    EVENT_CAN_RX,
    EVENT_CAN_TX,
    EVENT_EBM,
    EVENT_ERRSTATE,
    EventRecorder,
    RecordedBus,
    hook_ebm,
    hook_errstate,
)


def make_msg(arb_id=0x123, data=b"\x01\x02", name="test_frame"):
    """构造极简 Message 替身（仅含 record_frame 用到的字段）。"""
    return SimpleNamespace(arbitration_id=arb_id, data=data, dlc=len(data), name=name)


# ---- 基础记录 ----


def test_record_event_basic():
    r = EventRecorder()
    e = r.record_event(
        EVENT_CAN_TX,
        arb_id=0x100,
        direction="tx",
        category="frame",
        message="hello",
        payload={"data": "0102"},
        ts=1.0,
    )
    assert e["type"] == EVENT_CAN_TX
    assert e["arb_id"] == 0x100
    assert e["direction"] == "tx"
    assert e["category"] == "frame"
    assert e["message"] == "hello"
    assert e["payload"] == {"data": "0102"}
    assert len(r) == 1


def test_record_event_auto_timestamp_order():
    r = EventRecorder()
    r.record_event(EVENT_EBM, ts=3.0)
    r.record_event(EVENT_EBM, ts=1.0)
    r.record_event(EVENT_EBM, ts=2.0)
    # 查询按写入顺序返回（缓冲保持写入序，时间戳由调用方保证单调）
    assert [e["ts"] for e in r.query()] == [3.0, 1.0, 2.0]


def test_record_event_payload_copied():
    r = EventRecorder()
    payload = {"a": 1}
    r.record_event(EVENT_EBM, payload=payload, ts=1.0)
    payload["a"] = 99  # 外部改动不影响已入库事件
    assert r.query()[0]["payload"] == {"a": 1}


@pytest.mark.parametrize("bad", ["unknown", "can", "", "EBM"])
def test_record_event_rejects_unknown_type(bad):
    r = EventRecorder()
    with pytest.raises(ValueError):
        r.record_event(bad, ts=1.0)


def test_record_event_rejects_bad_direction():
    r = EventRecorder()
    with pytest.raises(ValueError):
        r.record_event(EVENT_CAN_TX, direction="up", ts=1.0)


def test_capacity_validation():
    with pytest.raises(ValueError):
        EventRecorder(capacity=0)
    with pytest.raises(ValueError):
        EventRecorder(capacity=-1)


def test_ring_buffer_drops_oldest():
    r = EventRecorder(capacity=3)
    for i in range(5):
        r.record_event(EVENT_EBM, message=f"e{i}", ts=float(i))
    assert len(r) == 3
    messages = [e["message"] for e in r.query()]
    assert messages == ["e2", "e3", "e4"]  # 最旧两条被环形淘汰


def test_protected_events_survive_frame_flood():
    """安全事件优先驻留：缓冲被普通帧占满时，写入保护事件不被挤出。"""
    r = EventRecorder(capacity=5)
    for i in range(5):
        r.record_event(EVENT_CAN_TX, arb_id=0x100, message=f"f{i}", ts=float(i))
    assert len(r) == 5
    # 再写入一个保护事件 → 淘汰最旧普通帧，保护事件保留
    r.record_event(EVENT_EBM, message="brake", ts=5.0)
    messages = [e["message"] for e in r.query()]
    assert messages == ["f1", "f2", "f3", "f4", "brake"]  # f0 被淘汰，保护事件保留
    assert len(r) == 5
    # 连续保护事件写入：容量封顶后保护事件也淘汰（内存不膨胀）
    for i in range(6, 12):
        r.record_event(EVENT_EBM, message=f"e{i}", ts=float(i))
    assert len(r) == 5
    assert r.stats()["dropped"] == 7  # 洪泛淘汰 f0-f4 共 5 + 保护封顶淘汰 brake/e6 共 2


def test_dropped_counts_written_and_dropped():
    r = EventRecorder(capacity=4)
    for i in range(10):
        r.record_event(EVENT_CAN_RX, arb_id=0x200, ts=float(i))
    s = r.stats()
    assert s["written"] == 10
    assert s["dropped"] == 6
    assert s["total"] == 4
    assert len(r) == 4


# ---- 查询过滤 ----


@pytest.fixture()
def filled_recorder():
    r = EventRecorder(capacity=100)
    r.record_event(
        EVENT_CAN_TX,
        arb_id=0x100,
        direction="tx",
        category="frame",
        message="brake_cmd",
        ts=1.0,
        payload={"data": "aabb"},
    )
    r.record_event(
        EVENT_CAN_RX, arb_id=0x200, direction="rx", category="frame", message="status", ts=2.0
    )
    r.record_event(
        EVENT_EBM, category="ebm_action", message="trigger", ts=3.0, payload={"reason": "overspeed"}
    )
    r.record_event(
        EVENT_ERRSTATE,
        category="state_change",
        message="error-passive",
        ts=4.0,
        payload={"tec": 128, "rec": 0},
    )
    return r


def test_query_no_filter_returns_all(filled_recorder):
    assert len(filled_recorder.query()) == 4


def test_query_by_type(filled_recorder):
    assert len(filled_recorder.query(event_type=EVENT_CAN_TX)) == 1
    frames = filled_recorder.query(event_type=EVENT_CAN_RX)
    assert frames[0]["arb_id"] == 0x200


def test_query_by_arb_id(filled_recorder):
    hits = filled_recorder.query(arb_id=0x100)
    assert len(hits) == 1
    assert hits[0]["type"] == EVENT_CAN_TX
    assert filled_recorder.query(arb_id=0xFFFF) == []


def test_query_by_direction_and_category(filled_recorder):
    assert len(filled_recorder.query(direction="rx")) == 1
    frames = filled_recorder.query(category="frame")
    assert len(frames) == 2
    assert filled_recorder.query(direction="tx", category="frame")[0]["message"] == "brake_cmd"


def test_query_by_time_window(filled_recorder):
    hits = filled_recorder.query(start_ts=2.0, end_ts=3.5)
    assert [e["message"] for e in hits] == ["status", "trigger"]


def test_query_by_text(filled_recorder):
    hits = filled_recorder.query(text="aabb")
    assert len(hits) == 1
    assert hits[0]["arb_id"] == 0x100
    # 未出现的文本不命中
    assert filled_recorder.query(text="0x100") == []


def test_query_limit(filled_recorder):
    assert len(filled_recorder.query(limit=2)) == 2


def test_query_combined(filled_recorder):
    hits = filled_recorder.query(
        event_type=EVENT_CAN_TX,
        arb_id=0x100,
        direction="tx",
        category="frame",
        start_ts=0.0,
        end_ts=10.0,
    )
    assert len(hits) == 1


# ---- record_frame / 统计 ----


def test_record_frame_tx_and_rx():
    r = EventRecorder()
    r.record_frame(make_msg(arb_id=0x321, data=b"\xde\xad"), "tx", node="ebm")
    r.record_frame(make_msg(arb_id=0x222), "rx", node="errstate")
    tx, rx = r.query()
    assert tx["type"] == EVENT_CAN_TX and tx["direction"] == "tx"
    assert tx["arb_id"] == 0x321
    assert tx["payload"]["data"] == "dead"
    assert tx["payload"]["node"] == "ebm"
    assert rx["type"] == EVENT_CAN_RX
    assert rx["payload"]["node"] == "errstate"


def test_stats_aggregates(filled_recorder):
    s = filled_recorder.stats()
    assert s["total"] == 4
    assert s["by_type"] == {"can_tx": 1, "can_rx": 1, "ebm": 1, "errstate": 1}
    assert s["by_direction"] == {"tx": 1, "rx": 1}
    assert s["by_category"] == {"frame": 2, "ebm_action": 1, "state_change": 1}
    assert s["by_arb_id"] == {0x100: 1, 0x200: 1}
    assert s["frames"] == 2
    assert s["bytes_total"] == 2  # aabb = 2 字节


def test_stats_empty():
    s = EventRecorder().stats()
    assert s["total"] == 0
    assert s["bytes_total"] == 0


# ---- 导出 ----


def test_export_json_roundtrip(filled_recorder):
    text = filled_recorder.export_json()
    parsed = json.loads(text)
    assert len(parsed) == 4
    assert parsed[1]["arb_id"] == 0x200


def test_export_json_to_file(filled_recorder, tmp_path):
    p = tmp_path / "events.json"
    filled_recorder.export_json(str(p))
    raw = p.read_bytes()
    assert raw.startswith(b"[\n")  # 无 BOM
    assert json.loads(raw.decode("utf-8"))[0]["type"] == EVENT_CAN_TX


def test_export_csv_columns_and_rows(filled_recorder):
    text = filled_recorder.export_csv()
    lines = text.strip().splitlines()
    assert lines[0] == "ts,type,direction,arb_id,category,message,payload"
    assert len(lines) == 5  # header + 4 事件
    row = lines[1].split(",")
    assert row[1] == EVENT_CAN_TX
    assert row[2] == "tx"
    assert row[3] == "256"  # 0x100


def test_export_csv_to_file_utf8(filled_recorder, tmp_path):
    p = tmp_path / "events.csv"
    filled_recorder.export_csv(str(p))
    text = p.read_text(encoding="utf-8")
    assert text.startswith("ts,type")


# ---- RecordedBus 装饰器 ----


class FakeBus:
    """充当 python-can Bus 的最小替身。"""

    def __init__(self):
        self.sent = []
        self.queue = []
        self.channel = "fake"

    def send(self, msg, timeout=None):
        self.sent.append(msg)
        return True

    def recv(self, timeout=None):
        return self.queue.pop(0) if self.queue else None


def test_query_returns_deep_copy_not_internal_reference():
    """查询结果可被外部修改而不污染库内事件（证据链完整性）。"""
    r = EventRecorder()
    r.record_event(EVENT_EBM, message="brake", ts=1.0)
    hit = r.query()[0]
    hit["message"] = "tampered"
    hit["payload"]["x"] = 1
    assert r.query()[0]["message"] == "brake"
    assert "x" not in r.query()[0]["payload"]


def test_recorded_bus_records_send():
    rec = EventRecorder()
    bus = RecordedBus(FakeBus(), rec, node="ebm")
    bus.send(make_msg(arb_id=0x100, data=b"\x01"))
    events = rec.query(event_type=EVENT_CAN_TX)
    assert len(events) == 1
    assert events[0]["payload"]["node"] == "ebm"
    assert rec.stats()["bytes_total"] == 1


def test_recorded_bus_send_failure_records_nothing():
    """发送失败不留假记录：send 抛异常/返回 False 均不记录。"""

    class BrokenBus(FakeBus):
        def send(self, msg, timeout=None):
            raise OSError("bus down")

    rec = EventRecorder()
    bus = RecordedBus(BrokenBus(), rec)
    with pytest.raises(OSError):
        bus.send(make_msg(arb_id=0x100))
    assert len(rec.query(event_type=EVENT_CAN_TX)) == 0

    class FailingBus(FakeBus):
        def send(self, msg, timeout=None):
            return False

    rec2 = EventRecorder()
    bus2 = RecordedBus(FailingBus(), rec2)
    assert bus2.send(make_msg(arb_id=0x100)) is False
    assert len(rec2.query(event_type=EVENT_CAN_TX)) == 0


def test_recorded_bus_records_recv_and_skips_timeout():
    rec = EventRecorder()
    raw = FakeBus()
    raw.queue = [make_msg(arb_id=0x200)]
    bus = RecordedBus(raw, rec, node="errstate")
    got = bus.recv(timeout=0.1)
    assert got.arbitration_id == 0x200
    assert len(rec.query(event_type=EVENT_CAN_RX)) == 1
    assert bus.recv(timeout=0.1) is None  # 超时无帧 → 不产生记录
    assert len(rec.query(event_type=EVENT_CAN_RX)) == 1


def test_recorded_bus_proxies_attributes():
    rec = EventRecorder()
    bus = RecordedBus(FakeBus(), rec)
    assert bus.channel == "fake"  # 代理到底层 bus


def test_recorded_bus_exposes_recorder():
    rec = EventRecorder()
    bus = RecordedBus(FakeBus(), rec)
    assert bus.recorder is rec


# ---- EBM / errstate 接线 ----


def test_hook_ebm_records_actions_and_preserves_result():
    rec = EventRecorder()
    mgr = EmergencyBrakeManager()
    hook_ebm(mgr, rec)
    result = mgr.trigger("overspeed")  # 返回值不受包装影响
    assert result["applied"] is True
    assert result["action"] == "emergency_brake"
    events = rec.query(event_type=EVENT_EBM)
    assert len(events) >= 1
    trigger = events[0]
    assert trigger["message"] == "trigger"
    assert trigger["payload"]["result"]["reason"] == "overspeed"
    assert trigger["payload"]["result"]["applied"] is True


def test_hook_ebm_records_release_cycle():
    rec = EventRecorder()
    mgr = EmergencyBrakeManager()
    hook_ebm(mgr, rec)
    mgr.trigger("overspeed")
    mgr.update_reason_status("overspeed", False)
    assert mgr.release_condition(0.0) is True
    msgs = [e["message"] for e in rec.query(event_type=EVENT_EBM)]
    assert msgs == ["trigger", "update_reason_status", "release_condition"]


def test_hook_errstate_records_migrations():
    rec = EventRecorder()
    sm = CanErrorStateMachine()
    hook_errstate(sm, rec, node="elcu")
    for _ in range(16):
        sm.tx_error()
    assert sm.state == STATE_ERROR_PASSIVE
    events = rec.query(event_type=EVENT_ERRSTATE)
    assert len(events) == 1
    assert events[0]["message"] == "error-passive"
    assert events[0]["payload"]["tec"] == 128
    assert events[0]["payload"]["node"] == "elcu"


def test_hook_errstate_keeps_existing_listener():
    seen = []
    sm = CanErrorStateMachine(on_state_change=seen.append)
    rec = EventRecorder()
    hook_errstate(sm, rec)
    for _ in range(16):
        sm.tx_error()
    assert seen == [STATE_ERROR_PASSIVE]  # 原监听器仍生效（链式）
    assert len(rec.query(event_type=EVENT_ERRSTATE)) == 1


def test_full_integration_timeline():
    """统一时间线：帧 + EBM 动作 + 错误状态迁移按顺序共处一条时间线。"""
    rec = EventRecorder()
    mgr = EmergencyBrakeManager()
    sm = CanErrorStateMachine()
    hook_ebm(mgr, rec)
    hook_errstate(sm, rec, node="elcu")
    bus = RecordedBus(FakeBus(), rec, node="tcms")

    bus.send(make_msg(arb_id=0x100, data=b"\x01"))  # can_tx
    mgr.trigger("overspeed")  # ebm
    sm.tx_error()  # errstate（还 active）
    for _ in range(15):
        sm.tx_error()
    mgr.update_reason_status("overspeed", False)  # ebm
    assert mgr.release_condition(0.0) is True  # ebm
    # 注：hook_ebm 包装后 release_condition 也被记录

    events = rec.query()
    types = [e["type"] for e in events]
    assert types[0] == EVENT_CAN_TX
    assert EVENT_EBM in types and EVENT_ERRSTATE in types
    err_events = rec.query(event_type=EVENT_ERRSTATE)
    assert err_events[0]["message"] == "error-passive"
    # 时间线完整：帧在 EBM 动作之前
    assert events[0]["arb_id"] == 0x100


def test_export_after_hooks_is_json_serializable():
    rec = EventRecorder()
    mgr = EmergencyBrakeManager()
    hook_ebm(mgr, rec)
    mgr.trigger("overspeed")
    parsed = json.loads(rec.export_json())
    assert parsed[0]["payload"]["result"]["action"] == "emergency_brake"


# ---- 事故冻结窗口（快照） ----


def test_freeze_snapshot_returns_windowed_events():
    rec = EventRecorder()
    for ts in (1.0, 2.0, 3.0, 4.0, 5.0, 10.0):
        rec.record_event(EVENT_CAN_TX, arb_id=0x100, ts=ts)
    snap = rec.freeze_snapshot(trigger_ts=3.0, before_s=1.5, after_s=1.0)
    assert snap["trigger_ts"] == 3.0
    assert snap["window"] == (1.5, 4.0)
    # 窗口 [1.5, 4.0] 内的事件：2.0/3.0/4.0
    assert [e["ts"] for e in snap["events"]] == [2.0, 3.0, 4.0]
    assert snap["counts"] == {EVENT_CAN_TX: 3}


def test_freeze_snapshot_does_not_mutate_buffer():
    rec = EventRecorder()
    for ts in (1.0, 2.0, 3.0):
        rec.record_event(EVENT_CAN_TX, arb_id=0x100, ts=ts)
    before = len(rec)
    rec.freeze_snapshot(trigger_ts=2.0, before_s=1.0, after_s=1.0)
    assert len(rec) == before  # 快照只读


def test_freeze_snapshot_filter_category():
    rec = EventRecorder()
    rec.record_event(EVENT_CAN_TX, arb_id=0x100, category="frame", ts=1.0)
    rec.record_event(EVENT_EBM, category="ebm_action", message="trigger", ts=2.0)
    rec.record_event(EVENT_CAN_RX, arb_id=0x200, category="frame", ts=3.0)
    snap = rec.freeze_snapshot(trigger_ts=2.0, before_s=1.0, after_s=1.0, category="frame")
    assert len(snap["events"]) == 2
    assert all(e["category"] == "frame" for e in snap["events"])


def test_snapshot_at_ebm_trigger_uses_last_trigger():
    import time

    rec = EventRecorder()
    mgr = EmergencyBrakeManager()
    hook_ebm(mgr, rec)
    rec.record_event(EVENT_CAN_TX, arb_id=0x100, ts=time.monotonic())  # 背景流量
    mgr.trigger("overspeed")  # 触发（ts≈monotonic）
    snap = rec.snapshot_at_ebm_trigger(mgr)
    assert snap["trigger_ts"] is not None
    # 快照包含触发事件本身 + 背景帧
    assert any(e["message"] == "trigger" for e in snap["events"])
    assert any(e["arb_id"] == 0x100 for e in snap["events"])


def test_snapshot_at_ebm_trigger_no_trigger_yet():
    rec = EventRecorder()
    mgr = EmergencyBrakeManager()
    hook_ebm(mgr, rec)
    mgr.update_reason_status("overspeed", True)  # 非 trigger 动作
    snap = rec.snapshot_at_ebm_trigger(mgr)
    assert snap["trigger_ts"] is None
    assert snap["events"] == []


def test_snapshot_exports_to_json():
    rec = EventRecorder()
    for ts in (1.0, 2.0, 3.0):
        rec.record_event(EVENT_CAN_TX, arb_id=0x100, ts=ts)
    snap = rec.freeze_snapshot(trigger_ts=2.0, before_s=1.0, after_s=1.0)
    text = json.dumps(snap, ensure_ascii=False, default=str)
    assert "trigger_ts" in text and "events" in text
