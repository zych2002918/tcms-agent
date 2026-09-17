"""TCMS 节点健康监视：心跳看门狗 + 节点健康表。

对应真实 TCMS 的标准监督机制：接收端连续 N 个周期未收到心跳即判节点
离线（含迟滞，避免瞬时抖动误判）；节点恢复后需连续若干次有效心跳
才回到在线——与真实列控网络的节点监督逻辑一致。
"""

import time

STATE_OFFLINE = "offline"
STATE_ONLINE = "online"
STATE_FAULT = "fault"


class NodeWatchdog:
    """单个节点的看门狗：喂心跳 + 周期评估健康状态。"""

    def __init__(
        self,
        cycle_time: float = 0.1,
        miss_threshold: int = 3,
        recover_threshold: int = 2,
        now=time.monotonic,
    ):
        self.cycle_time = cycle_time
        self.miss_threshold = miss_threshold  # 连续 N 周期未收 → Fault
        self.recover_threshold = recover_threshold  # 恢复需连续 M 次有效心跳
        self._now = now
        self._last_seen: float | None = None
        self._recover_count = 0
        self._state = STATE_OFFLINE

    # ---- 对外接口 ----

    def feed(self) -> None:
        """收到一帧该节点的心跳时调用。"""
        self._last_seen = self._now()
        if self._state in (STATE_OFFLINE, STATE_FAULT):
            self._recover_count += 1
            if self._recover_count >= self.recover_threshold:
                self._state = STATE_ONLINE
                self._recover_count = 0
        else:
            self._recover_count = 0

    def evaluate(self) -> str:
        """周期评估健康状态，返回 offline / online / fault。"""
        if self._last_seen is None:
            self._state = STATE_OFFLINE
            return self._state
        gap = self._now() - self._last_seen
        if self._state == STATE_ONLINE and gap > self.cycle_time * self.miss_threshold:
            self._state = STATE_FAULT
        return self._state

    @property
    def state(self) -> str:
        return self._state


class NodeHealthTable:
    """节点健康表：维护多个节点的看门狗，供全总线健康检查。"""

    def __init__(
        self,
        cycle_time: float = 0.1,
        miss_threshold: int = 3,
        recover_threshold: int = 2,
        now=time.monotonic,
    ):
        self.cycle_time = cycle_time
        self.miss_threshold = miss_threshold
        self.recover_threshold = recover_threshold
        self._now = now
        self._watchdogs: dict[str, NodeWatchdog] = {}

    def feed(self, node: str) -> None:
        """收到某节点心跳。"""
        self._watchdogs.setdefault(
            node,
            NodeWatchdog(self.cycle_time, self.miss_threshold, self.recover_threshold, self._now),
        ).feed()

    def evaluate(self) -> dict[str, str]:
        """评估全部节点，返回 {节点名: 状态}。"""
        return {node: wd.evaluate() for node, wd in self._watchdogs.items()}

    def status(self, node: str) -> str:
        return self._watchdogs.get(
            node,
            NodeWatchdog(self.cycle_time, self.miss_threshold, self.recover_threshold, self._now),
        ).state
