"""总线级故障注入（Bus-Level Fault Injection）—— 对标真实 CAN 物理层故障。

真实 CAN 总线物理层故障：
    - 短路（short）：总线对地/电源短路 → 所有节点失去仲裁能力，集体进入 Bus-Off
    - 断路（open）：总线断开 → 段内节点互相隔离，发送方无法收到 ACK → Bus-Off
    - 干扰（interference）：外部电磁干扰 → 错误帧激增 → 节点计数上升

本模块在虚拟总线上模拟这些故障，并验证节点的错误状态机联动：
    总线故障 → 节点检测到错误（TEC/REC 上升）→ 集体 Bus-Off → 故障恢复 → 节点复位。

设计原则：总线是共享介质——物理层故障影响**所有**节点（与单节点故障注入区分）。
"""

from . import errstate

# 故障类型
FAULT_SHORT = "short"  # 短路（对地/电源）
FAULT_OPEN = "open"  # 断路
FAULT_INTERFERENCE = "interference"  # 电磁干扰

VALID_FAULTS = (FAULT_SHORT, FAULT_OPEN, FAULT_INTERFERENCE)

# 模拟参数：单次故障注入导致的错误计数增量（对应一次错误帧）
SHORT_ERROR_INCREMENT = 8
OPEN_ERROR_INCREMENT = 8
INTERFERENCE_ERROR_INCREMENT = 8

# 恢复所需总线空闲次数（对齐 errstate.BUS_IDLE_RECOVERY）
RECOVERY_IDLE_COUNT = errstate.BUS_IDLE_RECOVERY


class BusFaultInjector:
    """总线级故障注入器：管理多个节点的错误状态机，模拟集体故障与恢复。

    用法：
        bfi = BusFaultInjector()
        bfi.add_node("VCU")
        bfi.inject(FAULT_SHORT)          # 短路：所有节点 TEC 上升 → Bus-Off
        bfi.recover()                    # 故障移除 + 空闲恢复
    """

    def __init__(self, recovery_idle_count: int = RECOVERY_IDLE_COUNT):
        self._nodes: dict[str, errstate.CanErrorStateMachine] = {}
        self._fault: str | None = None
        self._recovery_idle = recovery_idle_count

    @property
    def active_fault(self) -> str | None:
        """当前活动故障类型（None 表示无故障）。"""
        return self._fault

    @property
    def node_names(self) -> list[str]:
        return list(self._nodes)

    def add_node(self, name: str) -> errstate.CanErrorStateMachine:
        """添加一个节点（错误状态机实例）。返回该实例供外部操作。"""
        if name in self._nodes:
            raise ValueError(f"节点已存在: {name}")
        sm = errstate.CanErrorStateMachine()
        self._nodes[name] = sm
        return sm

    def _all_affected(self):
        return list(self._nodes.values())

    def inject(self, fault: str) -> None:
        """注入总线故障：所有节点错误计数持续上升至 Bus-Off。

        短路/断路 → 每个节点视为连续发送错误（TEC 反复 +8 直至 ≥256 → Bus-Off）。
        干扰 → 接收错误为主（REC 上升，不触发 Bus-Off，但污染通信）。
        """
        if fault not in VALID_FAULTS:
            raise ValueError(f"未知故障类型: {fault}（合法 {VALID_FAULTS}）")
        if self._fault is not None:
            raise RuntimeError(f"已有活动故障: {self._fault}（先 recover 再注入）")
        self._fault = fault
        for sm in self._all_affected():
            if fault == FAULT_INTERFERENCE:
                # 干扰：接收错误持续上升（不触发 Bus-Off）
                for _ in range(SHORT_ERROR_INCREMENT):
                    sm.rx_error("crc_error")
            else:
                # 短路/断路：持续发送错误直至 Bus-Off（TEC ≥ 256 需要 32 次 +8）
                for _ in range(32):
                    sm.tx_error("ack_error")

    def bus_off_nodes(self) -> list[str]:
        """当前处于 Bus-Off 的节点列表。"""
        return [n for n, sm in self._nodes.items() if sm.state == errstate.STATE_BUS_OFF]

    def recover(self, idle_bits: int = RECOVERY_IDLE_COUNT) -> None:
        """故障恢复：移除故障 + 每个 Bus-Off 节点累计空闲位复位。

        短路/断路恢复：节点在总线空闲若干次后 TEC/REC 归零回 Error-Active。
        干扰恢复：无 Bus-Off，仅清故障标记。
        """
        self._fault = None
        for sm in self._all_affected():
            if sm.state == errstate.STATE_BUS_OFF:
                sm.bus_idle_bit(idle_bits)

    def status_report(self) -> dict:
        """状态报告：各节点状态 + 活动故障。"""
        return {
            "fault": self._fault,
            "nodes": {n: sm.state for n, sm in self._nodes.items()},
        }
