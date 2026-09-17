"""CAN 错误状态机（TEC/REC / Error-Active / Error-Passive / Bus-Off）—— 对标 ISO 11898-1。

真实 CAN 节点的错误管理核心：每个节点维护两个 8 位错误计数器，
    TEC —— 发送错误计数（Transmit Error Counter）
    REC —— 接收错误计数（Receive Error Counter）
根据计数落入三种状态：
    Error-Active   TEC < 128 且 REC < 128   正常参与总线，检出错误发主动错误标志
    Error-Passive  TEC >= 128 或 REC >= 128 只能发被动错误标志（本实现仅建模
                   状态与计数；被动标志帧/发送前退避时序属物理层，不建模）
    Bus-Off        TEC >= 256               完全退出总线，不参与任何通信

计数增减规则（ISO 11898-1 错误处理，测试版简化但规则对齐）：
    - 发送错误：TEC += 8；接收错误：REC += 8
    - 成功发送：TEC 在 1..127 时 -1；TEC >= 128 时直接置 120（被动快速回归）
    - 成功接收：REC 在 1..127 时 -1；REC >= 128 时直接置 119（被动快速回归）
    - 计数器 8 位封顶 255；TEC 越过 256 触发 Bus-Off（Bus-Off 仅由发送错误引发）
    - Bus-Off 恢复：累计 128 次总线空闲（11 位隐性位周期）后 TEC/REC 归零复位

同时按错误类型（位/填充/CRC/格式/ACK）统计损坏帧计数，
对应真实 CAN 网络的"总线错误帧统计"（CANoe 等工具的同款指标）。
"""

ERROR_TYPES = ("bit_error", "stuff_error", "crc_error", "form_error", "ack_error")

STATE_ERROR_ACTIVE = "error-active"
STATE_ERROR_PASSIVE = "error-passive"
STATE_BUS_OFF = "bus-off"
VALID_STATES = (STATE_ERROR_ACTIVE, STATE_ERROR_PASSIVE, STATE_BUS_OFF)

ERROR_ACTIVE_MAX = 127  # TEC/REC 低于此值视为 Error-Active
ERROR_PASSIVE_MIN = 128  # TEC/REC 达到此值转入 Error-Passive
BUS_OFF_THRESHOLD = 256  # TEC 达到此值触发 Bus-Off（仅发送错误）
COUNTER_MAX = 255  # 8 位计数器封顶
ERROR_INCREMENT = 8  # 检出一次错误计数 +8
TX_SUCCESS_PASSIVE_TARGET = 120  # TEC>=128 成功发送后直接置 120
RX_SUCCESS_PASSIVE_TARGET = 119  # REC>=128 成功接收后直接置 119
BUS_IDLE_RECOVERY = 128  # Bus-Off 恢复所需总线空闲位（11 位隐性位周期）计数
SUSPEND_TRANSMIT_BITS = 8  # 恢复后发送前退避（suspend transmission）位数


class CanErrorStateMachine:
    """单个 CAN 节点的错误状态机：错误注入 → 计数迁移 → Bus-Off → 恢复。"""

    def __init__(self, on_state_change=None):
        self._tec: int = 0
        self._rec: int = 0
        self._bus_idle: int = 0
        self._suspend_bits: int = 0
        self._state = STATE_ERROR_ACTIVE
        self._listeners: list = []
        if on_state_change is not None:
            self._listeners.append(on_state_change)
        # 损坏帧统计（对应真实工具的总线错误统计）
        self.error_frames: int = 0
        self.error_counts: dict[str, int] = {kind: 0 for kind in ERROR_TYPES}

    # ---- 只读属性 ----

    @property
    def state(self) -> str:
        """当前状态：error-active / error-passive / bus-off。"""
        return self._state

    @property
    def tec(self) -> int:
        """发送错误计数（8 位封顶）。"""
        return self._tec

    @property
    def rec(self) -> int:
        """接收错误计数（8 位封顶）。"""
        return self._rec

    @property
    def bus_idle(self) -> int:
        """Bus-Off 后已累计的总线空闲位计数（恢复进度）。"""
        return self._bus_idle

    # ---- 内部 ----

    def _set_state(self, new_state: str) -> None:
        if new_state != self._state:
            self._state = new_state
            for listener in self._listeners:
                listener(new_state)

    def add_state_listener(self, listener) -> None:
        """注册附加状态迁移监听器（链式；构造参数注册的监听器一并保留）。"""
        self._listeners.append(listener)

    def _validate_kind(self, kind: str) -> None:
        if kind not in ERROR_TYPES:
            raise ValueError(f"未知错误类型: {kind}（合法类型 {ERROR_TYPES}）")

    def _reevaluate(self) -> None:
        """按计数重新判定 active/passive（Bus-Off 状态由 TEC>=256 独占触发）。"""
        if self._state == STATE_BUS_OFF:
            return
        if self._tec >= ERROR_PASSIVE_MIN or self._rec >= ERROR_PASSIVE_MIN:
            self._set_state(STATE_ERROR_PASSIVE)
        else:
            self._set_state(STATE_ERROR_ACTIVE)

    # ---- 错误/成功事件注入 ----

    def tx_error(self, kind: str = "ack_error") -> str:
        """发送错误（如无 ACK、位错误）：TEC += 8，可能触发 Bus-Off。返回当前状态。

        Bus-Off 期间节点已退出总线，不感知也不累计任何错误（no-op）。
        """
        self._validate_kind(kind)
        if self._state == STATE_BUS_OFF:
            return self._state
        self.error_counts[kind] += 1
        self.error_frames += 1
        self._tec += ERROR_INCREMENT
        if self._tec >= BUS_OFF_THRESHOLD:
            self._tec = COUNTER_MAX  # 8 位封顶存储；Bus-Off 判据已满足
            self._set_state(STATE_BUS_OFF)
        else:
            self._tec = min(self._tec, COUNTER_MAX)
            self._reevaluate()
        return self._state

    def rx_error(self, kind: str = "crc_error") -> str:
        """接收错误（如 CRC/填充/格式）：REC += 8。接收错误不触发 Bus-Off。"""
        self._validate_kind(kind)
        if self._state == STATE_BUS_OFF:
            return self._state
        self.error_counts[kind] += 1
        self.error_frames += 1
        self._rec = min(self._rec + ERROR_INCREMENT, COUNTER_MAX)
        self._reevaluate()
        return self._state

    def tx_success(self) -> str:
        """成功发送一帧（收到 ACK、无错误）：TEC -= 1；若 TEC>=128 直接置 120。"""
        if self._state == STATE_BUS_OFF:
            return self._state
        if self._tec >= ERROR_PASSIVE_MIN:
            self._tec = TX_SUCCESS_PASSIVE_TARGET
        elif self._tec > 0:
            self._tec -= 1
        self._reevaluate()
        return self._state

    def rx_success(self) -> str:
        """成功接收一帧：REC -= 1；若 REC>=128 直接置 119。"""
        if self._state == STATE_BUS_OFF:
            return self._state
        if self._rec >= ERROR_PASSIVE_MIN:
            self._rec = RX_SUCCESS_PASSIVE_TARGET
        elif self._rec > 0:
            self._rec -= 1
        self._reevaluate()
        return self._state

    # ---- 恢复 ----

    def bus_idle_bit(self, count: int = 1) -> str:
        """Bus-Off 恢复进度：累计总线空闲位，达到 128 次后复位为 Error-Active。

        非 Bus-Off 状态下调用为 no-op（幂等）。
        """
        if self._state != STATE_BUS_OFF:
            return self._state
        self._bus_idle += max(0, count)
        if self._bus_idle >= BUS_IDLE_RECOVERY:
            self._tec, self._rec, self._bus_idle = 0, 0, 0
            self._set_state(STATE_ERROR_ACTIVE)
            # 恢复后进入发送前退避（Error-Passive 发送前需等待 8 位隐性位）
            self._suspend_bits = SUSPEND_TRANSMIT_BITS
        return self._state

    def tx_backoff_remaining(self) -> int:
        """恢复后发送前退避剩余位数（Error-Active 恢复后 8 位隐性位）。

        真实 ISO 11898-1：Error-Passive 节点发送前必须等待 8 位隐性位
        （suspend transmission），Bus-Off 恢复后同样需先退避再发送。
        """
        return self._suspend_bits

    def tx_backoff_bit(self, count: int = 1) -> None:
        """退避进度：累计隐性位，退避完成后可发送。"""
        self._suspend_bits = max(0, self._suspend_bits - max(0, count))

    def reset(self) -> str:
        """软件复位：清除全部错误计数并立即恢复 Error-Active（诊断/测试用）。"""
        self._tec, self._rec, self._bus_idle = 0, 0, 0
        self._suspend_bits = 0
        self._set_state(STATE_ERROR_ACTIVE)
        return self._state
