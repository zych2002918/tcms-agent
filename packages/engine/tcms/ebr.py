"""EBR 紧急制动硬线回路仿真 —— 独立于 CAN 的 SIL4 执行路径。

真实列车的紧急制动请求（Emergency Brake Request, EBR）走**硬线回路**，
而不是 CAN 总线：一条贯穿全列的常闭触点回路（司机手柄、ATP/ATO 触点、
紧急制动按钮等常闭触点串联），回路**得电=缓解、失电=制动**。

为什么不用 CAN：通信网络可能故障（节点 Bus-Off、网关崩溃、线缆破损），
紧急制动是 SIL4 安全功能，其执行路径必须独立于可失效的通信介质——
这正是 EBM 中 hardwire_loss 原因对应的"硬线备份"的现实依据。

本模块建模：
    - 串联常闭触点：任一触点开路（制动请求）→ 回路失电 → 制动施加
    - 物理断线：所有触点闭合但回路仍失电 → 诊断推理为断线（非请求源开路）
    - 断线自检：diag_pulse 周期注入检测，开路可预警（fail-safe 方向不误报缓解）
    - 双回路 2oo2 冗余：主/备两条回路，任一失电即制动，单条断线不损失制动能力
"""

# 回路状态
LOOP_ENERGIZED = "energized"  # 得电 → 制动缓解
LOOP_DEENERGIZED = "de-energized"  # 失电 → 紧急制动施加

DIAG_OK = "ok"
DIAG_OPEN_REQUEST = "open_by_request"  # 触点开路（正常制动请求）
DIAG_WIRE_BREAK = "wire_break"  # 物理断线（故障）


class EbrLoop:
    """单条 EBR 硬线回路：串联常闭触点 + 断线检测。"""

    def __init__(
        self,
        name: str = "EBR-A",
        contacts: tuple[str, ...] = ("driver_handle", "atp_contact", "emergency_btn"),
    ):
        self.name = name
        self._contacts: dict[str, bool] = {c: True for c in contacts}
        self._wire_broken = False

    # ---- 触点操作（制动请求源） ----

    def open_contact(self, name: str) -> None:
        """触点开路 = 制动请求（如按下紧急按钮、ATP 输出 EB 命令）。"""
        if name not in self._contacts:
            raise ValueError(f"未知触点: {name}（{tuple(self._contacts)}）")
        self._contacts[name] = False

    def close_contact(self, name: str) -> None:
        """触点闭合 = 请求解除。"""
        if name not in self._contacts:
            raise ValueError(f"未知触点: {name}（{tuple(self._contacts)}）")
        self._contacts[name] = True

    # ---- 物理层故障 ----

    def break_wire(self) -> None:
        """回路断线（线缆破损/接头松脱）——物理故障，非制动请求。"""
        self._wire_broken = True

    def repair_wire(self) -> None:
        """修复断线。"""
        self._wire_broken = False

    # ---- 回路状态 ----

    @property
    def energized(self) -> bool:
        """回路是否得电（触点全部闭合且无断线）。"""
        return (not self._wire_broken) and all(self._contacts.values())

    @property
    def state(self) -> str:
        return LOOP_ENERGIZED if self.energized else LOOP_DEENERGIZED

    @property
    def brake_applied(self) -> bool:
        """失电即施加紧急制动（fail-safe：故障方向=制动方向）。"""
        return not self.energized

    @property
    def wire_broken(self) -> bool:
        return self._wire_broken

    @property
    def open_contacts(self) -> tuple[str, ...]:
        """当前开路的触点（制动请求源）。"""
        return tuple(c for c, closed in self._contacts.items() if not closed)

    # ---- 诊断 ----

    def diag_pulse(self) -> str:
        """回路自检：区分"触点开路的正常制动请求"与"物理断线故障"。

        推理依据：请求源全部闭合（无人请求制动）但回路仍失电，
        只可能是物理断线——诊断逻辑与列车 EBR 回路监测一致。
        """
        if self.energized:
            return DIAG_OK
        if self._wire_broken:
            return DIAG_WIRE_BREAK
        return DIAG_OPEN_REQUEST

    def diagnose_wire_break(self) -> bool:
        """断线诊断：所有触点闭合但回路失电 → 必为断线。"""
        return self._wire_broken and all(self._contacts.values())


class EbrLoopPair:
    """双回路 2oo2 冗余：主/备两条 EBR 回路。

    任一回路失电即施加紧急制动（fail-safe，宁可误制动不可漏制动）；
    单条断线时另一条回路仍保证制动能力，仅产生检修预警。
    """

    def __init__(self, loop_a: EbrLoop, loop_b: EbrLoop):
        if loop_a is loop_b:
            raise ValueError("两条回路必须是独立实例")
        self.loop_a = loop_a
        self.loop_b = loop_b

    @property
    def brake_applied(self) -> bool:
        """2oo2：任一回路失电 → 紧急制动施加。"""
        return self.loop_a.brake_applied or self.loop_b.brake_applied

    @property
    def degraded(self) -> bool:
        """单回路故障降级（另一回路仍可保证制动能力）。"""
        return self.loop_a.wire_broken != self.loop_b.wire_broken

    def health(self) -> dict:
        """回路健康摘要（状态 + 失电原因 + 降级标志）。"""
        return {
            "loop_a": {"state": self.loop_a.state, "diag": self.loop_a.diag_pulse()},
            "loop_b": {"state": self.loop_b.state, "diag": self.loop_b.diag_pulse()},
            "brake_applied": self.brake_applied,
            "degraded": self.degraded,
        }

    def repair(self) -> None:
        """检修：修复两条回路的断线（不触碰触点请求）。"""
        self.loop_a.repair_wire()
        self.loop_b.repair_wire()
