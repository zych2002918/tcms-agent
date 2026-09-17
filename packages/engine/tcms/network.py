"""多网段拓扑抽象（A6）—— 多总线 + 异步缓冲网关。

对标真实列控网络：MVB/TRDP/以太网并存的多网段结构，不同子系统
（牵引/制动/门控/信号）挂在不同网段上，网段之间通过**网关**按报文
ID 过滤转发。

现实语义（非同步透传）：
    网关是独立设备，不是"send 时立即转发"的函数调用。真实列车网关
    的行为是：
        1. 监听源网段，把到达的报文收进自己的**接收缓冲（FIFO）**；
        2. 按自己的**扫描周期/处理时延（latency）**处理缓冲；
        3. 到期的帧按过滤表（白名单/黑名单）决定转发或丢弃；
        4. 缓冲满时**新帧被丢弃**（溢出），不阻塞源网段发送方；
        5. 帧跨网关扩散有**足迹防环**（同一帧不重复经过同一网关，
           对标网桥 STP 剪枝 / TRDP 跳数限制）。
    因此本模块的转发是**异步的**：`send()` 只把帧投递到网关缓冲，
    `step()`（由虚拟时钟驱动）才让网关把到期帧转发到目标网段——
    转发时延、缓冲溢出、级联扩散全部可观测、可测试、可审计。

设计原则（红线内）：
    - 只做拓扑抽象：Segment（命名总线）+ Gateway（异步缓冲转发）
    - 不重写 MVB/TRDP 协议，不绑定任何真实协议细节
    - 网关转发规则 = 报文 ID 白名单/黑名单（对标真实网关的报文过滤表）
    - 转发路径可统计、可审计（面试点：拓扑可观测性）

用法：
    clock = VirtualClock(mode="virtual")
    net = BusNetwork({"propulsion": bus_a, "brake": bus_b}, clock=clock)
    net.add_gateway("gw1", src="propulsion", dst="brake",
                    allow_ids=[proto.TCMS_HEARTBEAT], latency=0.02)
    net.send("propulsion", msg)   # 只投递到网关缓冲，不立即转发
    clock.advance(0.02)
    net.step()                    # 网关泵：到期帧转发到 brake 段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import can
from can import Bus, Message

from tcms import timebase

# 转发规则类型
RULE_ALLOW = "allow"  # 白名单：仅在列表中
RULE_BLOCK = "block"  # 黑名单：不在列表中

# 丢弃原因（审计）
DROP_RULE = "rule"  # 被过滤表拒绝
DROP_OVERFLOW = "overflow"  # 缓冲溢出，新帧被丢弃
DROP_LOOP = "loop"  # 足迹防环：同一帧重复经过同一网关


@dataclass
class _Pending:
    """网关缓冲中的待转发帧（内部记录，不污染 Message 对象）。"""

    msg: Message
    enqueued_ts: float  # 入缓冲时刻（帧的真实到达时间语义）
    due_ts: float  # 到期时刻 = enqueued_ts + latency
    trace: frozenset[str]  # 已经过的网关足迹（防环）


@dataclass
class Gateway:
    """网段间报文网关：独立设备，异步缓冲转发。

    属性：
        name:       网关名（审计用）
        src:        源网段名（监听侧）
        dst:        目标网段名（转发侧）
        allow_ids:  白名单（rule=allow 时生效；为空 = 全部转发）
        block_ids:  黑名单（rule=block 时生效；为空 = 全部转发）
        rule:       RULE_ALLOW / RULE_BLOCK
        latency:    转发处理时延（秒），对标网关扫描周期
        capacity:   接收缓冲容量（帧数），满则溢出丢弃新帧
        forwarded:  累计成功转发帧数（可观测性）
        dropped:    累计被过滤表拒绝帧数（可观测性）
        overflow_dropped: 累计缓冲溢出丢弃帧数（可观测性）
    """

    name: str
    src: str
    dst: str
    allow_ids: set[int] = field(default_factory=set)
    block_ids: set[int] = field(default_factory=set)
    rule: str = RULE_ALLOW
    latency: float = 0.0
    capacity: int = 64
    forwarded: int = 0
    dropped: int = 0
    overflow_dropped: int = 0
    _buffer: list[_Pending] = field(default_factory=list, repr=False)

    def should_forward(self, arb_id: int) -> bool:
        """按规则判定某报文 ID 是否应转发（过滤表查询）。"""
        if self.rule == RULE_ALLOW:
            return arb_id in self.allow_ids if self.allow_ids else True
        return arb_id not in self.block_ids

    # ---- 设备内部行为 ----

    def enqueue(self, msg: Message, now: float, trace: frozenset[str]) -> bool:
        """接收缓冲入队（监听源网段得到帧）。

        - 足迹防环：帧已在本网关处理过 → 拒绝（返回 False，不计溢出）
        - 缓冲满：新帧丢弃（返回 False，计 overflow_dropped）
        - 成功入队：记录入队时刻，到期时刻 = now + latency
        """
        if self.name in trace:
            return False
        if len(self._buffer) >= self.capacity:
            self.overflow_dropped += 1
            return False
        self._buffer.append(
            _Pending(
                msg=msg,
                enqueued_ts=now,
                due_ts=now + self.latency,
                trace=trace,
            )
        )
        return True

    def pump(self, now: float) -> list[_Pending]:
        """处理缓冲中已到期的帧（网关扫描一拍），返回待转发列表。"""
        due = [p for p in self._buffer if p.due_ts <= now]
        if due:
            self._buffer = [p for p in self._buffer if p.due_ts > now]
        return due

    @property
    def buffered(self) -> int:
        """当前缓冲中待处理的帧数。"""
        return len(self._buffer)


class BusNetwork:
    """多网段拓扑：命名总线集合 + 异步网关互联 + 时钟驱动转发。

    发送路径：net.send(segment, msg)
        → 本地段总线立即出现该帧（真实 CAN 语义）
        → 所有 src==segment 的网关缓冲收帧（时延后由 step 转发）
    转发路径：net.step()
        → 每个网关泵出到期帧 → 过滤表判定 → 转发到目标段
        → 目标段的出站网关继续收帧（级联扩散，足迹防环）
    隔离验证：无网关的两段互不可见（测试"网段隔离"）
    """

    def __init__(self, buses: dict[str, Bus], clock=None):
        if not buses:
            raise ValueError("至少需要一个网段")
        self._buses: dict[str, Bus] = dict(buses)
        self._clock = clock or timebase.global_clock()
        self._gateways: dict[str, Gateway] = {}
        self._by_src: dict[str, list[str]] = {}  # src → 网关名列表
        self._forward_log: list[dict] = []  # 审计日志（深拷贝语义）

    # ---- 拓扑构建 ----

    def add_segment(self, name: str, bus: Bus) -> None:
        """追加网段（运行时热插拔）。"""
        if name in self._buses:
            raise ValueError(f"网段已存在: {name}")
        self._buses[name] = bus

    def add_gateway(
        self,
        name: str,
        src: str,
        dst: str,
        allow_ids: Iterable[int] | None = None,
        block_ids: Iterable[int] | None = None,
        rule: str = RULE_ALLOW,
        latency: float = 0.0,
        capacity: int = 64,
    ) -> Gateway:
        """添加网关。网段必须已存在且 src != dst。"""
        if name in self._gateways:
            raise ValueError(f"网关已存在: {name}")
        if src not in self._buses:
            raise ValueError(f"源网段不存在: {src}")
        if dst not in self._buses:
            raise ValueError(f"目标网段不存在: {dst}")
        if src == dst:
            raise ValueError("网关源/目标不能同一网段")
        if rule not in (RULE_ALLOW, RULE_BLOCK):
            raise ValueError(f"未知规则: {rule}")
        if latency < 0:
            raise ValueError(f"转发时延不能为负: {latency}")
        if capacity < 1:
            raise ValueError(f"缓冲容量至少为 1: {capacity}")
        gw = Gateway(
            name,
            src,
            dst,
            allow_ids=set(allow_ids or []),
            block_ids=set(block_ids or []),
            rule=rule,
            latency=latency,
            capacity=capacity,
        )
        self._gateways[name] = gw
        self._by_src.setdefault(src, []).append(name)
        return gw

    # ---- 数据面 ----

    def send(self, segment: str, msg: Message) -> bool:
        """发送报文到指定网段。

        本地段总线立即出现该帧；网段间转发是**异步**的——
        帧先进入出站网关的接收缓冲，由 `step()` 按时延转发。

        返回是否成功发送到本地段（转发结果在网关统计中）。
        """
        if segment not in self._buses:
            raise ValueError(f"网段不存在: {segment}")
        bus = self._buses[segment]
        try:
            bus.send(msg)
            ok = True
        except can.CanError:
            ok = False
        now = self._clock.now()
        for gw_name in self._by_src.get(segment, ()):
            self._gateways[gw_name].enqueue(msg, now, frozenset())
        return ok

    def step(self) -> None:
        """驱动所有网关转发一拍（由时钟驱动，对标网关扫描周期）。

        每个网关泵出到期帧：过滤表判定 → 转发到目标段 →
        目标段出站网关继续收帧（级联扩散，足迹防环）。
        """
        now = self._clock.now()
        for gw in self._gateways.values():
            for pending in gw.pump(now):
                self._forward_one(gw, pending, now)

    def _forward_one(self, gw: Gateway, pending: _Pending, now: float) -> None:
        msg = pending.msg
        if not gw.should_forward(msg.arbitration_id):
            gw.dropped += 1
            self._forward_log.append(
                {
                    "gateway": gw.name,
                    "src": gw.src,
                    "dst": gw.dst,
                    "arb_id": msg.arbitration_id,
                    "enqueued_ts": pending.enqueued_ts,
                    "forwarded_ts": now,
                    "forwarded": False,
                    "dropped_reason": DROP_RULE,
                }
            )
            return
        dst_bus = self._buses[gw.dst]
        try:
            dst_bus.send(msg)
            dst_ok = True
        except can.CanError:
            dst_ok = False
        gw.forwarded += 1
        self._forward_log.append(
            {
                "gateway": gw.name,
                "src": gw.src,
                "dst": gw.dst,
                "arb_id": msg.arbitration_id,
                "enqueued_ts": pending.enqueued_ts,
                "forwarded_ts": now,
                "forwarded": dst_ok,
                "dropped_reason": None,
            }
        )
        # 级联扩散：转发到目标段的帧，继续进入目标段出站网关的缓冲
        trace = pending.trace | {gw.name}
        for gw_name in self._by_src.get(gw.dst, ()):
            self._gateways[gw_name].enqueue(msg, now, trace)

    def recv_from(self, segment: str, timeout: float = 0.0) -> Message | None:
        """从指定网段接收一条报文（本地段视角）。"""
        if segment not in self._buses:
            raise ValueError(f"网段不存在: {segment}")
        return self._buses[segment].recv(timeout=timeout)

    def recv_any(self, timeout: float = 0.0) -> tuple[str, Message] | None:
        """跨网段接收：返回 (网段名, 报文)，按段名排序轮询。

        用于"全网段统一视角"（对标真实测试台的网络级监控）。
        """
        import time

        for name in sorted(self._buses):
            msg = self._buses[name].recv(timeout=0.0)
            if msg is not None:
                return name, msg
        # 全部空时再阻塞等 timeout：小步轮询全部段，避免只盯第一段
        if timeout > 0:
            deadline = time.monotonic() + timeout
            names = sorted(self._buses)
            while True:
                for name in names:
                    msg = self._buses[name].recv(timeout=0.01)
                    if msg is not None:
                        return name, msg
                if time.monotonic() >= deadline:
                    return None
        return None

    # ---- 管理面 ----

    @property
    def segments(self) -> list[str]:
        return sorted(self._buses)

    @property
    def gateways(self) -> list[str]:
        return list(self._gateways)

    def gateway_stats(self) -> dict[str, dict]:
        """网关统计（转发/过滤丢弃/溢出丢弃/缓冲占用）。"""
        return {
            name: {
                "forwarded": gw.forwarded,
                "dropped": gw.dropped,
                "overflow_dropped": gw.overflow_dropped,
                "buffered": gw.buffered,
            }
            for name, gw in self._gateways.items()
        }

    def forward_log(self) -> list[dict]:
        """转发审计日志（深拷贝，防止外部篡改）。

        记录每帧被网关处理的结果：转发成功 / 过滤表拒绝 /
        足迹防环拒绝（入队失败），含入队/处理时间戳。
        """
        return [dict(e) for e in self._forward_log]

    def reset_stats(self) -> None:
        """清零网关计数与转发日志（缓冲保留）。"""
        for gw in self._gateways.values():
            gw.forwarded = 0
            gw.dropped = 0
            gw.overflow_dropped = 0
        self._forward_log.clear()
