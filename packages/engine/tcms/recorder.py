"""事件时序记录器（Recorder）—— 总线帧与系统事件的统一时序记录。

对标真实工具链与工程实践的定位：
    - CAN 分析工具的 Logging/Trace 窗口（CANoe、PCAN-View）：每条总线事件按
      时间戳连续记录，供事后回放与排查
    - 轨道交通信号系统的"事件记录器"（对标 EN 50128 语境下的可追溯性要求）：
      制动请求、模式降级、故障状态等安全事件必须按时间顺序留存，作为
      故障分析与安全论证的证据链

核心能力：
    - 环形缓冲（deque(maxlen)），长时间运行内存不膨胀；**安全事件（EBM/错误状态
      迁移）优先驻留**——缓冲满时先淘汰普通帧，安全事件不被洪泛流量挤出
    - 统一 record_event 入口：CAN 帧/EBM/错误状态机都往同一时间线写
    - RecordedBus：python-can Bus 装饰器，透明记录收发帧（发送成功后才记录）
    - hook_ebm / hook_errstate：把紧急制动管理与错误状态机的事件接到时间线
    - query 过滤：类型 / 仲裁 ID / 方向 / 类别 / 时间段 / 关键词（返回深拷贝，
      外部修改不影响证据）
    - stats 聚合统计（含真实丢帧计数）；export_json / export_csv 导出
      （UTF-8 无 BOM，CSV 兼容 Excel）
"""

import copy
import csv
import functools
import io
import json
import time
from collections import Counter, deque

# 事件类型常量（统一时间线的分类口径）
EVENT_CAN_TX = "can_tx"  # 节点发出的一帧
EVENT_CAN_RX = "can_rx"  # 节点收到的一帧
EVENT_EBM = "ebm"  # 紧急制动管理动作
EVENT_ERRSTATE = "errstate"  # CAN 错误状态机状态迁移
VALID_EVENT_TYPES = (EVENT_CAN_TX, EVENT_CAN_RX, EVENT_EBM, EVENT_ERRSTATE)

# 受保护事件类型：缓冲满时优先保留（安全事件不被总线洪泛挤出）
PROTECTED_EVENT_TYPES = (EVENT_EBM, EVENT_ERRSTATE)

DEFAULT_CAPACITY = 10000  # 环形缓冲默认容量


class EventRecorder:
    """按时间顺序记录事件并提供过滤查询/统计/导出的环形缓冲记录器。"""

    def __init__(
        self, capacity: int = DEFAULT_CAPACITY, protected_types: tuple = PROTECTED_EVENT_TYPES
    ):
        if capacity <= 0:
            raise ValueError(f"capacity 必须为正数，got {capacity}")
        self._events: deque = deque(maxlen=capacity)
        self._capacity = capacity
        self._protected_types = protected_types
        self._written = 0  # 累计写入（含被淘汰）
        self._dropped = 0  # 真实淘汰计数

    # ---- 记录 ----

    def record_event(
        self,
        event_type: str,
        arb_id: int | None = None,
        direction: str | None = None,
        category: str | None = None,
        message: str | None = None,
        payload: dict | None = None,
        ts: float | None = None,
    ) -> dict:
        """写入一条事件。ts 缺省取当前单调时钟；返回落库的事件记录。

        event_type 必须是合法常量之一；其余字段均为可选元数据。
        缓冲满时优先淘汰最旧的**非保护**事件；若全部为保护事件则淘汰最旧保护
        事件（容量封顶，长时间运行内存不膨胀）。
        """
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"未知事件类型: {event_type}（合法类型 {VALID_EVENT_TYPES}）")
        if direction is not None and direction not in ("tx", "rx"):
            raise ValueError(f"direction 必须为 tx/rx/None，got {direction!r}")
        if ts is None:
            ts = time.monotonic()
        event = {
            "ts": ts,
            "type": event_type,
            "arb_id": arb_id,
            "direction": direction,
            "category": category,
            "message": message,
            "payload": dict(payload) if payload else {},
        }
        self._append(event)
        return event

    def _append(self, event: dict) -> None:
        self._written += 1
        if len(self._events) == self._capacity:
            if any(e["type"] not in self._protected_types for e in self._events):
                # 优先淘汰最旧的非保护事件
                for i, e in enumerate(self._events):
                    if e["type"] not in self._protected_types:
                        del self._events[i]
                        self._dropped += 1
                        break
            else:
                self._events.popleft()  # 全保护：按容量封顶淘汰最旧
                self._dropped += 1
        self._events.append(event)

    def record_frame(
        self, msg, direction: str, node: str | None = None, source: str | None = None
    ) -> dict:
        """记录一条 python-can Message（can_tx / can_rx）。"""
        return self.record_event(
            EVENT_CAN_TX if direction == "tx" else EVENT_CAN_RX,
            arb_id=msg.arbitration_id,
            direction=direction,
            category="frame",
            message=getattr(msg, "name", None),
            payload={
                "node": node,
                "dlc": msg.dlc,
                "data": bytes(msg.data).hex(),
                "source": source,
            },
        )

    # ---- 查询 ----

    def query(
        self,
        event_type: str | None = None,
        arb_id: int | None = None,
        direction: str | None = None,
        category: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        text: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """按条件过滤并返回按时间排序的事件列表（不影响缓冲内容）。

        返回深拷贝：外部修改查询结果不污染库内事件（证据链不可篡改）。
        """
        out = []
        for e in self._events:
            if event_type is not None and e["type"] != event_type:
                continue
            if arb_id is not None and e["arb_id"] != arb_id:
                continue
            if direction is not None and e["direction"] != direction:
                continue
            if category is not None and e["category"] != category:
                continue
            if start_ts is not None and e["ts"] < start_ts:
                continue
            if end_ts is not None and e["ts"] > end_ts:
                continue
            if text is not None:
                blob = json.dumps(e, ensure_ascii=False, default=str)
                if text not in blob:
                    continue
            out.append(copy.deepcopy(e))
            if limit is not None and len(out) >= limit:
                break
        return out

    # ---- 统计 ----

    def stats(self) -> dict:
        """聚合统计：总数、按类型/方向/类别/仲裁 ID 分布、总字节数。"""
        by_type = Counter(e["type"] for e in self._events)
        by_direction = Counter(e["direction"] for e in self._events if e["direction"] is not None)
        by_category = Counter(e["category"] for e in self._events if e["category"] is not None)
        by_arb_id = Counter(e["arb_id"] for e in self._events if e["arb_id"] is not None)
        frames = [e for e in self._events if e["type"] in (EVENT_CAN_TX, EVENT_CAN_RX)]
        bytes_total = sum(
            len(e["payload"].get("data", "")) // 2 for e in frames if e["payload"].get("data")
        )
        return {
            "total": len(self._events),
            "capacity": self._capacity,
            "written": self._written,  # 累计写入（含被淘汰）
            "dropped": self._dropped,  # 真实淘汰/丢帧计数
            "by_type": dict(by_type),
            "by_direction": dict(by_direction),
            "by_category": dict(by_category),
            "by_arb_id": dict(by_arb_id),
            "frames": len(frames),
            "bytes_total": bytes_total,
        }

    # ---- 事故冻结窗口（对标真实事件记录仪的"冻结快照"） ----

    def freeze_snapshot(
        self,
        trigger_ts: float,
        before_s: float = 5.0,
        after_s: float = 5.0,
        event_type: str | None = None,
        category: str | None = None,
    ) -> dict:
        """事故冻结窗口：触发时刻前后 N 秒的事件快照（只读，不改变缓冲）。

        对标真实 TCMS 事件记录仪：EB 触发/严重故障时自动冻结前后一段时间
        的记录，供事后事故分析（黑匣子语义）——本方法对任意时刻生成快照，
        供调用方在触发瞬间立即导出并留存（证据链不可篡改）。

        参数:
            trigger_ts: 触发时刻（单调时钟）
            before_s / after_s: 冻结窗口的前/后秒数
            event_type / category: 可选过滤（只冻结指定类型/类别）
        返回:
            {"trigger_ts", "window", "before_s", "after_s",
             "events": [...], "counts": {...}}
        """
        events = self.query(
            event_type=event_type,
            category=category,
            start_ts=trigger_ts - before_s,
            end_ts=trigger_ts + after_s,
        )
        counts = Counter(e["type"] for e in events)
        return {
            "trigger_ts": trigger_ts,
            "window": (trigger_ts - before_s, trigger_ts + after_s),
            "before_s": before_s,
            "after_s": after_s,
            "events": events,
            "counts": dict(counts),
        }

    def snapshot_at_ebm_trigger(
        self, manager, before_s: float = 5.0, after_s: float = 5.0, category: str | None = None
    ) -> dict:
        """EBM 触发瞬间冻结快照（识别最后一次紧急制动触发时刻）。

        扫描 EBM 事件中的 trigger 动作，取最后一次触发时刻作为冻结锚点；
        无触发记录时返回空快照（trigger_ts=None）。
        """
        trigs = [
            e
            for e in self._events
            if e["type"] == EVENT_EBM
            and e.get("category") == "ebm_action"
            and e.get("message") == "trigger"
        ]
        if not trigs:
            return {
                "trigger_ts": None,
                "window": None,
                "before_s": before_s,
                "after_s": after_s,
                "events": [],
                "counts": {},
            }
        anchor = trigs[-1]["ts"]
        return self.freeze_snapshot(anchor, before_s, after_s, event_type=None, category=category)

    # ---- 导出 ----

    def export_json(self, path: str | None = None) -> str:
        """导出为 JSON 字符串；给定 path 时同时写文件（UTF-8 无 BOM）。"""
        text = json.dumps(list(self._events), ensure_ascii=False, indent=2)
        if path is not None:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        return text

    def export_csv(self, path: str | None = None) -> str:
        """导出为 CSV 时间线（ts,type,direction,arb_id,category,message,payload）。"""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["ts", "type", "direction", "arb_id", "category", "message", "payload"])
        for e in self._events:
            writer.writerow(
                [
                    f"{e['ts']:.6f}",
                    e["type"],
                    e["direction"] or "",
                    e["arb_id"] if e["arb_id"] is not None else "",
                    e["category"] or "",
                    e["message"] or "",
                    json.dumps(e["payload"], ensure_ascii=False, default=str),
                ]
            )
        text = buf.getvalue()
        if path is not None:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        return text

    # ---- 容器协议 ----

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)


class RecordedBus:
    """python-can Bus 装饰器：自动把收发帧写入事件记录器。

    代理底层 bus 的 send/recv 之外所有属性与方法；
    recv 无帧可收（返回 None / 超时）时不产生记录。
    """

    def __init__(self, bus, recorder: EventRecorder, node: str | None = None):
        self._bus = bus
        self._recorder = recorder
        self._node = node

    def send(self, msg, *args, **kwargs):
        result = self._bus.send(msg, *args, **kwargs)
        # 发送成功后才记录：发送失败（异常/返回失败）不留假记录
        if result is not False:
            self._recorder.record_frame(msg, "tx", node=self._node, source="app")
        return result

    def recv(self, *args, **kwargs):
        msg = self._bus.recv(*args, **kwargs)
        if msg is not None:
            self._recorder.record_frame(msg, "rx", node=self._node, source="bus")
        return msg

    def __getattr__(self, name):
        return getattr(self._bus, name)

    @property
    def recorder(self) -> EventRecorder:
        return self._recorder


# ---- 与 EBM / errstate 的互操作（接线适配器，不改动被接模块） ----


def hook_ebm(
    manager,
    recorder: EventRecorder,
    methods=(
        "trigger",
        "set_mode",
        "update_reason_status",
        "release_condition",
        "self_heal",
        "reset",
    ),
) -> None:
    """把 EmergencyBrakeManager 的公开动作逐一写入事件记录器。

    用 functools.wraps 包装方法（保留原方法元数据与外部调用语义），
    EBM 模块本身零改动，职责单向：EBM 仍是决策主体。
    """
    for name in methods:
        fn = getattr(manager, name)

        @functools.wraps(fn)
        def wrapper(*args, _name=name, _fn=fn, **kwargs):
            result = _fn(*args, **kwargs)
            recorder.record_event(
                EVENT_EBM,
                category="ebm_action",
                message=_name,
                payload={"args": _fmt_args(_fn, args, kwargs), "result": result},
            )
            return result

        setattr(manager, name, wrapper)


def hook_errstate(sm, recorder: EventRecorder, node: str | None = None) -> None:
    """把 CanErrorStateMachine 的状态迁移写入事件记录器（追加监听器）。"""

    def listener(state):
        recorder.record_event(
            EVENT_ERRSTATE,
            category="state_change",
            message=state,
            payload={"node": node, "tec": sm.tec, "rec": sm.rec},
        )

    sm.add_state_listener(listener)


def _fmt_args(fn, args, kwargs) -> dict:
    """把方法调用参数转成可 JSON 序列化的瘦包装（避免大对象入时间线）。"""
    bound = {}
    try:
        for name, value in zip(fn.__code__.co_varnames[1:], args):
            bound[name] = value
    except AttributeError:  # 无 __code__ 的可调用对象（如内建/部分绑定）
        bound["args"] = list(args)
    bound.update({k: v for k, v in kwargs.items()})
    for key in list(bound):
        v = bound[key]
        if not isinstance(v, (str, int, float, bool)) or v is None:
            if isinstance(v, (dict, list)):
                bound[key] = json.dumps(v, ensure_ascii=False, default=str)[:200]
            else:
                bound[key] = str(v)[:200]
    return bound
