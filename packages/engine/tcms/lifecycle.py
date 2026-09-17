"""TCMS 节点生命周期状态机。

对应真实列车控制节点的上电-就绪-运行-故障过程（DBC 中 NodeStatus
枚举：PowerOff/Standby/Active/Fault）：

    PowerOff --上电--> Standby --就绪--> Active --异常--> Fault
        ^                                          |
        |________________复位______________________|

规则：
- 合法迁移表驱动，非法迁移抛出 ValueError（测试验证）
- 节点故障后必须复位回 Standby 才能重新就绪
"""

from dataclasses import dataclass

POWER_OFF = "PowerOff"
STANDBY = "Standby"
ACTIVE = "Active"
FAULT = "Fault"

# 合法迁移表: (from, to)
VALID_TRANSITIONS = {
    (POWER_OFF, STANDBY),  # 上电
    (STANDBY, ACTIVE),  # 自检通过/就绪
    (ACTIVE, FAULT),  # 检测到异常
    (ACTIVE, STANDBY),  # 正常降级/维护
    (FAULT, STANDBY),  # 复位
    (STANDBY, POWER_OFF),  # 下电
}

# 节点名 -> DBC NodeStatus 枚举值
STATUS_CODE = {POWER_OFF: 0, STANDBY: 1, ACTIVE: 2, FAULT: 3}


@dataclass
class NodeLifecycle:
    """单节点生命周期状态机。"""

    node: str
    state: str = POWER_OFF

    def transition(self, new_state: str) -> None:
        """迁移到新状态；非法迁移抛 ValueError。"""
        if (self.state, new_state) not in VALID_TRANSITIONS:
            raise ValueError(f"{self.node}: 非法状态迁移 {self.state} -> {new_state}")
        self.state = new_state

    def power_on(self) -> None:
        self.transition(STANDBY)

    def ready(self) -> None:
        self.transition(ACTIVE)

    def fail(self) -> None:
        self.transition(FAULT)

    def reset(self) -> None:
        self.transition(STANDBY)

    def power_off(self) -> None:
        self.transition(POWER_OFF)

    @property
    def status_code(self) -> int:
        """DBC 编码值。"""
        return STATUS_CODE[self.state]

    def __repr__(self) -> str:
        return f"NodeLifecycle({self.node}, {self.state})"
