"""CANopen NMT 心跳层（CiA 301）—— 对标 CANopen 网络管理的节点监督机制。

CANopen（CiA 301）是工业/车载常用高层协议，其**心跳协议（Heartbeat）**：
    - 每个节点周期性发送 1 字节心跳报文（COB-ID = 0x700 + node_id）
    - 心跳内容 = 节点状态（Boot-up 0x00 / Stopped 0x04 / Operational 0x05 / Pre-operational 0x7F）
    - 监督方（NMT Master）配置心跳消费超时（heartbeat consumer time），
      超时未收到 → 判定节点"心跳丢失"（heartbeat event）

本模块实现：
    - 心跳生产者：节点状态机 + 周期心跳生成
    - 心跳消费者：超时监督 → 节点状态判定（在线/丢失）
    - 节点状态迁移（对应 CANopen NMT 状态机：Initialisation → Pre-operational →
      Operational ↔ Stopped）

设计原则：心跳是"节点健康"的应用层机制（区别于 errstate 的物理层错误计数），
与项目看门狗（watchdogs.py）互补——CANopen 心跳带状态语义，不只是"存活"。
"""

# COB-ID 基址：心跳 = 0x700 + node_id；NMT 命令 = 0x000（node_id 1-127）
HEARTBEAT_COB_BASE = 0x700
NMT_COB_ID = 0x000
MIN_NODE_ID = 1
MAX_NODE_ID = 127

# 心跳内容（节点状态）
NMT_BOOTUP = 0x00  # 初始化完成（boot-up 消息，仅一次）
NMT_STOPPED = 0x04  # 停止（不参与通信）
NMT_OPERATIONAL = 0x05  # 运行（正常通信）
NMT_PRE_OPERATIONAL = 0x7F  # 预运行（可配置，不参与 PDO）

# NMT 主站命令（CiA 301：COB-ID 0x000，2 字节：命令 + 目标节点）
NMT_CMD_START = 0x01  # 启动 → Operational
NMT_CMD_STOP = 0x02  # 停止 → Stopped
NMT_CMD_ENTER_PRE_OPERATIONAL = 0x80  # 进入 Pre-operational
NMT_CMD_RESET_NODE = 0x81  # 复位节点
NMT_CMD_RESET_COMMUNICATION = 0x82  # 复位通信

# 默认心跳周期（ms，真实设备典型 100-1000ms）
DEFAULT_HEARTBEAT_MS = 100

# 状态
NODE_ONLINE = "online"
NODE_HEARTBEAT_LOST = "heartbeat_lost"


class HeartbeatProducer:
    """心跳生产者：节点状态 → 心跳字节（供虚拟总线发送）。"""

    def __init__(self, node_id: int, period_ms: int = DEFAULT_HEARTBEAT_MS):
        if not (MIN_NODE_ID <= node_id <= MAX_NODE_ID):
            raise ValueError(f"node_id 必须在 {MIN_NODE_ID}-{MAX_NODE_ID}（收到 {node_id}）")
        self.node_id = node_id
        self.period_ms = period_ms
        self.state: int = NMT_PRE_OPERATIONAL
        self._bootup_sent: bool = False

    @property
    def cob_id(self) -> int:
        """心跳报文 COB-ID（0x700 + node_id）。"""
        return HEARTBEAT_COB_BASE + self.node_id

    def set_state(self, state: int) -> None:
        """切换节点状态（合法 NMT 状态字节）。"""
        if state not in (NMT_STOPPED, NMT_OPERATIONAL, NMT_PRE_OPERATIONAL):
            raise ValueError(f"非法 NMT 状态字节: {state:#x}")
        self.state = state

    def heartbeat_payload(self) -> bytes:
        """生成心跳报文负载（1 字节状态）。首帧前先发 boot-up。"""
        if not self._bootup_sent:
            self._bootup_sent = True
            return bytes([NMT_BOOTUP])
        return bytes([self.state])

    def reset(self) -> None:
        """复位：回到 Pre-operational，boot-up 待重发。"""
        self.state = NMT_PRE_OPERATIONAL
        self._bootup_sent = False


class HeartbeatConsumer:
    """心跳消费者：监督节点心跳，超时判定心跳丢失。

    用法：
        hc = HeartbeatConsumer(period_ms=100, timeout_ms=300)
        hc.on_heartbeat(0x05)     # 收到 Operational 心跳
        hc.check_timeout()        # 距上次 > 300ms → heartbeat_lost
    """

    def __init__(self, period_ms: int = DEFAULT_HEARTBEAT_MS, timeout_ms: int | None = None):
        self.period_ms = period_ms
        self.timeout_ms = timeout_ms or period_ms * 3
        self._last_ts: float | None = None
        self._state: str = NODE_HEARTBEAT_LOST
        self._events: int = 0

    @property
    def state(self) -> str:
        return self._state

    def on_heartbeat(self, payload: bytes | int, timestamp_s: float) -> None:
        """收到心跳（payload 为状态字节）。记录时间与状态。"""
        byte = payload if isinstance(payload, int) else payload[0]
        if byte == NMT_BOOTUP:
            # boot-up 视为节点重新初始化，重置计时
            self._last_ts = timestamp_s
            self._state = NODE_ONLINE
            return
        self._last_ts = timestamp_s
        self._state = NODE_ONLINE

    def check_timeout(self, timestamp_s: float) -> str:
        """超时检查：距上次心跳 > timeout → heartbeat_lost（并计数事件）。"""
        if self._last_ts is not None and timestamp_s - self._last_ts > self.timeout_ms / 1000.0:
            if self._state == NODE_ONLINE:
                self._events += 1
            self._state = NODE_HEARTBEAT_LOST
        return self._state

    def reset(self) -> None:
        """复位：回到 lost 状态。"""
        self._last_ts = None
        self._state = NODE_HEARTBEAT_LOST
        self._events = 0


class NmtMaster:
    """NMT 主站：向从站发送状态管理命令（CiA 301 NMT 协议）。

    对标真实 CANopen 主站：通过 COB-ID 0x000 发送 2 字节命令帧
    （命令 + 目标 node_id），控制从站的状态机迁移。
    node_id=0 表示广播到全部从站。

    用法：
        master = NmtMaster()
        master.command_frame(NMT_CMD_START, node_id=5)   # (0x000, b'\\x01\\x05')
    """

    def __init__(self):
        self._command_log: list[dict] = []  # 命令审计日志

    @property
    def command_log(self) -> list[dict]:
        """已发出命令的审计日志（深拷贝，证据链）。"""
        return [dict(c) for c in self._command_log]

    def command_frame(self, command: int, node_id: int) -> tuple[int, bytes]:
        """构造 NMT 命令帧：返回 (COB-ID, 2 字节负载)。

        命令必须是合法 NMT 命令之一；node_id 必须在 0-127
        （0 = 广播）。
        """
        valid = (
            NMT_CMD_START,
            NMT_CMD_STOP,
            NMT_CMD_ENTER_PRE_OPERATIONAL,
            NMT_CMD_RESET_NODE,
            NMT_CMD_RESET_COMMUNICATION,
        )
        if command not in valid:
            raise ValueError(f"非法 NMT 命令: {command:#x}")
        if not 0 <= node_id <= MAX_NODE_ID:
            raise ValueError(f"node_id 必须在 0-{MAX_NODE_ID}（收到 {node_id}）")
        frame = (NMT_COB_ID, bytes([command, node_id]))
        self._command_log.append(
            {
                "command": command,
                "node_id": node_id,
                "cob_id": NMT_COB_ID,
                "payload": frame[1].hex(),
            }
        )
        return frame

    def apply_to_producer(self, command: int, producer: HeartbeatProducer) -> str | None:
        """把命令作用到单个心跳生产者（模拟从站收到命令后的状态迁移）。

        返回命令后的节点状态（或 None 表示无状态迁移的命令，如复位）。
        """
        if command == NMT_CMD_START:
            producer.set_state(NMT_OPERATIONAL)
            return NMT_OPERATIONAL
        if command == NMT_CMD_STOP:
            producer.set_state(NMT_STOPPED)
            return NMT_STOPPED
        if command == NMT_CMD_ENTER_PRE_OPERATIONAL:
            producer.set_state(NMT_PRE_OPERATIONAL)
            return NMT_PRE_OPERATIONAL
        if command == NMT_CMD_RESET_NODE:
            producer.reset()  # 回到 Pre-operational，boot-up 待重发
            return NMT_PRE_OPERATIONAL
        if command == NMT_CMD_RESET_COMMUNICATION:
            producer.reset()
            return NMT_PRE_OPERATIONAL
        raise ValueError(f"非法 NMT 命令: {command:#x}")
