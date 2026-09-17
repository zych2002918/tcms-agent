# TCMS-CAN-Test 完整讲解：从项目态势到技术细节

> 本教程以**完整态势**讲解项目：先讲清「为什么做」（背景）→「别人怎么做、差在哪」
> （同类现状）→「我们做什么」（项目目的）→「内部怎么构成」（全景）→「细节怎么实现」
> （分章深入）。读完 + 跑通示例，即具备独立、从头到尾讲解整个项目的能力。
> 配套：`README.md`（总览）、`docs/safety_case.md`（安全论证）、`docs/` 状态机图与演示动画。

---

## 第 1 章 背景：为什么要做这件事

### 1.1 列车 TCMS 是什么

**TCMS**（Train Control and Management System，列车控制与管理系统）是轨道交通列车的
"大脑与神经"：它通过**车载总线**把牵引、制动、车门、空调、信号等子系统连成一张控制网，
采集状态、执行控制、守护安全。

列车控制网络的核心载体之一是 **CAN 总线**（Controller Area Network）：

- **物理层**：双绞线差分信号，抗干扰强、成本低，广泛用于列车车辆级控制；
- **链路层**：CSMA/CA 仲裁，非破坏性位仲裁保证高优先级报文先发；
- **应用层**：由**报文 ID + 数据字节**构成，每个 ID 对应一种信号组合（如车速 0x200、
  车门状态 0x400），含义由 **DBC 协议数据库**定义。

### 1.2 为什么列车控制软件需要"测"

列车控制是**安全攸关（safety-critical）**系统——紧急制动、门联锁、超速防护一旦失效，
后果是人身安全事件。因此它遵循功能安全标准（EN 50126/50128/50129，SIL 等级），
要求：

1. **每条安全需求都有测试证据**（需求→实现→测试→结果闭环）；
2. **故障场景必须可复现**——注入故障、观察行为、回归验证；
3. **覆盖率可度量**——测试到底测到了多少代码路径。

### 1.3 现实痛点：故障难复现、验证靠人工

真实的列车 CAN 网络验证存在三个痛点：

| 痛点 | 表现 |
|------|------|
| **时序敏感** | 报文是 50/100/500ms 周期流，丢一帧、迟到一帧都可能是故障，人工盯不住 |
| **故障注入危险** | 在真车上注入"短路/断路/超速"验证，既不安全也不现实 |
| **回归成本高** | 安全逻辑改动后要重跑全部场景，没有自动化就没有回归保障 |

> **一句话背景**：列车控制安全攸关、时序敏感、故障难复现——需要一个**无硬件依赖、
> 可自动化、可复现、可度量覆盖率**的测试平台。

---

## 第 2 章 市面同类项目现状：别人怎么做、差在哪

### 2.1 工业工具链（成熟但封闭）

| 工具 | 定位 | 优势 | 局限 |
|------|------|------|------|
| **Vector CANoe** | 汽车/工业 CAN 开发测试旗舰 | 仿真/分析/诊断一体，生态完整 | 商业授权昂贵、闭源；黑盒——用户看不到"内部在算什么" |
| **PCAN/周立功配套软件** | 硬件配套分析工具 | 硬件接入简单 | 偏采集/监视，自动化测试脚本能力弱 |
| **ETAS/INCA** | ECU 标定/测量 | 与 ECU 开发流程耦合 | 重、贵、面向汽车量产 |

### 2.2 开源生态（能力分散）

| 方向 | 代表 | 优势 | 局限 |
|------|------|------|------|
| **协议解析** | python-can、cantools | 收发/编解码基础设施成熟 | 只提供"字节层面"能力，没有安全逻辑 |
| **仿真** | CANopen 栈、openDDS | 协议栈完整 | 面向通信本身，不面向"安全验证" |
| **测试框架** | pytest + hypothesis | 断言/属性测试强大 | 与"列车控制语义"脱节，需要自己建模 |

### 2.3 空白点：本项目切入的位置

把上面的现状叠起来看，存在一个明确空白：

> **开源世界缺少一个"把列车安全控制逻辑 + CAN 通信 + 自动化验证 + 覆盖率证据"
> 串成完整链路**的项目。CANoe 能测但闭源贵，python-can 能通信但不关心安全语义，
> pytest 能断言但不理解"紧急制动/门联锁"。

**TCMS-CAN-Test 的定位**：把 CANoe 的核心能力（协议解析、仿真、Trace、错误统计、
负载率分析）逐一对标**开源实现**，再往前一步——把**列车安全逻辑本身**（联锁/EBM/
EBR/ATP/看门狗）建模成可测的代码，用自动化测试形成证据链。它不替代 CANoe 做真实
车型开发，而是做一个**可解释、可复现、可上 CI 的安全验证平台**。

> **面试叙事**：CANoe 是成熟工具，但它不展示"我理解协议层在发生什么"。本项目把
> CANoe 的核心能力逐一对标实现，每一步都有物理/协议依据——证明"知道工具背后在算什么"。

---

## 第 3 章 项目目的：我们要交付什么

**一句话**：从零构建一个**开源、无硬件依赖、可复现、覆盖率可度量**的
列车 TCMS CAN 报文自动化测试平台。

具体交付（按价值排序）：

1. **协议层基础设施**：8 类报文 DBC 数据库 + 编解码封装——"列车的语言字典"；
2. **可注入的仿真 DUT**：单节点/多节点仿真器，支持节点失活、故障注入——
   "被测的列车控制单元"；
3. **安全逻辑内核**：门联锁、紧急制动管理（EBM）、EBR 硬线回路、执行反馈、
   CAN 错误状态机、ATP 速度监督、看门狗——"列车怎么保命"的代码化；
4. **测试与证据**：958 个 pytest 用例 + 覆盖率门禁 97% + CI（GitHub Actions）+
   Allure 报告——"测了、测够、能证明"；
5. **可复现性基础设施**：虚拟时间基 + 回放链 + 故障场景 DSL——"故障能复现、
   场景能声明、全程可审计"。

**红线（诚实边界）**：不还原真实车型协议（涉密）、不做真实 SIL 认证（演示级论证）、
不重写 MVB/TRDP 全栈。**目标不是"做出一个能用的商业工具"，而是"证明我理解这条
链路上的每一个环节，并且能把它用代码和测试落地"。**

---

## 第 4 章 内部构成：全景地图

```
┌─────────────────────────────────────────────────────────────────────┐
│                       TCMS-CAN-Test 全景                              │
│                                                                     │
│  协议层      tcms.dbc（8 个报文 + 信号 + 周期 + 枚举）              │
│     │        tcms/protocol.py（编码/解码封装）                         │
│     ▼                                                                 │
│  总线层      python-can 虚拟总线 ←→ 真实 CAN（环境变量切换）             │
│     │        tcms/bus.py、tcms/canlog.py、tcms/network.py（多网段）    │
│     ▼                                                                 │
│  仿真层      被测对象 DUT：单节点/多节点仿真器 + 故障注入                │
│     │        tcms/simulator.py、tcms/multinode.py                      │
│     ▼                                                                 │
│  安全逻辑层  联锁 → 紧急制动 → EBR 硬线 → 执行反馈 → 状态机             │
│     │        tcms/interlocks.py、ebm.py、ebr.py、exec_feedback.py、    │
│     │        errstate.py、atp.py、watchdogs.py、faultlevel.py          │
│     ▼                                                                 │
│  网络与时间  多网段拓扑、虚拟时间基、故障生命周期、回放链               │
│     │        tcms/network.py、timebase.py、faultlife.py、replay.py     │
│     ▼                                                                 │
│  验证层      pytest 套件（960 用例）+ hypothesis 属性测试              │
│             覆盖率门禁 97% + CI（GitHub Actions）+ Allure 报告          │
└─────────────────────────────────────────────────────────────────────┘
```

**核心心智模型**：这不是"一个测试工具"，而是一条**从协议定义到安全证据的完整链路**——
每个模块都在链路上占据一个位置。面试时按这条链路讲，逻辑自洽：

> 列车在"说什么"（协议层）→ 报文怎么"跑"（总线层）→ 谁在"发"（仿真层）→
> 收到后怎么"保命"（安全逻辑层）→ 怎么让它"可复现可审计"（网络与时间层）→
> 怎么证明"测够了"（验证层）。

---

## 第 5 章 细节一：协议层（dbc + protocol）

### 5.1 DBC 是什么

DBC（CAN Database）是 CAN 报文的"字典"：定义每个报文（帧）的 ID、周期、数据字节里
每个信号的位布局、取值范围、枚举含义。真实列车的控制逻辑就建立在这本字典上。

本项目 `tcms/tcms.dbc` 定义 8 个核心报文（模拟设计，非真实车型协议）：

| 报文 | ID | 周期 | 关键信号 |
|------|-----|------|---------|
| 心跳 TCMS_HEARTBEAT | 0x100 | 100ms | 节点存活标志 |
| 车速 VEHICLE_SPEED | 0x200 | 100ms | SpeedKmh(0.1km/h)、SpeedValid |
| 牵引制动手柄 TRACTION_BRAKE_HANDLE | 0x300 | 50ms | 手柄位置 |
| 车门控制 DOOR_CONTROL | 0x400 | 100ms | Door1~4State(各2bit) |
| 报警事件 ALARM_EVENT | 0x500 | 事件型 | AlarmCode、各报警标志 |
| 受电弓状态 PANTOGRAPH_STATUS | 0x600 | 500ms | 升降状态 |
| 制动系统 BRAKE_SYSTEM | 0x700 | 100ms | BrakeCylinderPressure |
| 能源状态 ENERGY_STATUS | 0x780 | 500ms | SOC |

### 5.2 信号怎么编码（读位布局）

DBC 信号定义形如 `SpeedKmh 0|16@1+ (0.1,0) [0|200] "km/h"`：

- `0|16@1+`：起始位 0、长度 16 位、`@1`=小端、`+`=无符号；
- `(0.1,0)`：缩放因子 0.1、偏移 0 → 原始值 1000 表示 100.0 km/h；
- `[0|200]`：物理值域 0~200 km/h。

`tcms/protocol.py` 用 cantools 封装：`encode("VehicleSpeed", SpeedKmh=100.0)` 自动完成
"物理值 → 位级编码"。

### 5.3 动手：读一个报文

```python
from tcms import protocol
frame = protocol.encode("VehicleSpeed", SpeedKmh=100.0, SpeedValid=1)
print(hex(frame.arbitration_id), frame.data.hex())   # 0x200 ...
```

### 5.4 测试视角（test_protocol.py）

验证 DBC 结构完整性、ID 唯一、周期属性、信号值域、枚举表——协议层是"需求文档"，
测试就是需求验收。

---

## 第 6 章 细节二：总线层（bus + canlog + network）

### 6.1 虚拟总线 vs 真实总线

python-can 提供 `interface="virtual"` 的环回总线，收发报文的接口与真实 CAN 卡
（PCAN/Vector/socketcan）完全一致。`tcms/bus.py` 把"用什么接口"抽象成环境变量：

```python
from tcms.bus import make_bus
bus = make_bus()                    # 默认 virtual
# 换真实硬件：设环境变量 TCMS_BUS_INTERFACE=socketcan 等，代码零改动
```

**同一套用例、两种执行环境**——这是 HIL（硬件在环）测试的基础设施设计。

### 6.2 CAN 日志（canlog）：真实数据入口

Vector .asc 格式日志 → `parse_asc()` 解析为帧列表 → `AscReplayer` 按时间戳回放。
真实列车日志（脱敏）可以直接喂给回放链做回归。

### 6.3 多网段拓扑（network）：列车不止一条总线

真实列车有牵引/制动/门控等多条总线，经**网关**按报文 ID 过滤互联。
网关是**独立设备**，不是"send 时立即转发"的函数调用——真实网关的行为是：
监听源网段 → 报文进接收缓冲（FIFO）→ 按扫描周期处理 → 过滤表判定转发/丢弃；
缓冲满时新帧溢出丢弃，不阻塞发送方。

```python
from can import Bus
from tcms import timebase
from tcms.network import BusNetwork

clock = timebase.VirtualClock(mode="virtual")   # 虚拟时钟确定性驱动
net = BusNetwork({"propulsion": Bus(interface="virtual", channel="p"),
                  "brake": Bus(interface="virtual", channel="b")}, clock=clock)
net.add_gateway("gw1", src="propulsion", dst="brake",
                allow_ids=[0x100], latency=0.02)  # 转发时延 20ms
net.send("propulsion", msg)        # 只投递到网关缓冲，不立即转发
clock.advance(0.02)
net.step()                         # 网关泵：到期帧按规则转发到 brake 段
net.gateway_stats()                # 转发/过滤丢弃/溢出丢弃/缓冲占用统计
```

**现实语义**：异步缓冲转发（时延）、溢出丢弃、足迹防环（帧不重复过同一网关，
对标网桥 STP 剪枝）、逐网关级联扩散——全部可观测、可测试、可审计。

**面试点**：网关 = 拓扑上的"门"（按 ID 过滤），不是协议转换器；转发路径可统计、
可审计——对标真实测试台的网络级监控。

---

## 第 7 章 细节三：仿真层（simulator + multinode）

### 7.1 单节点仿真器

`TCMSNodeSimulator` 按 DBC 周期自动往总线发报文，可设置车速/手柄/车门状态、
注入丢报、发报警事件。它就是"被测的列车控制单元"。

### 7.2 多节点：一条总线上的多个 ECU

`MultiNodeSimulator` 模拟 VCU（主控）/BCU（制动）/BMS（能源）三个独立节点，
每个节点按自己的报文周期工作；`disable_node()` 模拟断电/通信中断——
**节点失活 → 其报文消失**，这是后续看门狗/丢报检测的故障源。

```python
from tcms.multinode import MultiNodeSimulator
sim = MultiNodeSimulator(bus)
sim.start()
sim.disable_node("bms")          # BMS 失活：能源报文消失
sim.enable_node("bms")           # 恢复
sim.stop()
```

### 7.3 故障注入（faults + fault_injection）

`compute_crc8/flip_bit/corrupt_byte/corrupt_frame` 提供位级篡改能力，
`FaultInjector` 提供结构化注入编排——这是"测故障"的弹药库。

---

## 第 8 章 细节四：安全逻辑层（核心）

这一层是项目的灵魂，也是面试的深水区。按"**检测 → 决策 → 执行 → 反馈**"闭环理解：

### 8.1 联锁（interlocks）：不合法就不动

- 门-车联锁：开门/门故障时移动 = 违规（超速/移动阈值 0.5 km/h）；
- 超速-制动联锁：>160 km/h 触发紧急制动决策；
- 牵引-制动互锁、方向-速度联动、车门-站台联动等。

```python
from tcms.interlocks import door_motion_conflict
bad, reason = door_motion_conflict(door_states, speed_kmh=30.0, speed_valid=True)
```

### 8.2 紧急制动管理（ebm）：决策大脑

对标真实 TCMS"紧急制动原因表"：**模式 × 原因处置矩阵**，SIL2/SIL4 双通道表决。

| 设计点 | 内容 |
|--------|------|
| 8 原因 × 3 模式（FAM/CM/RM） | 超速全模式制动；车门在 RM 豁免（司机人工确认） |
| SIL4（超速/ATP故障/门开） | 双通道**任一触发即制动**（故障安全：宁可错杀） |
| SIL2（ATO故障/火灾） | 双通道**一致才制动**（防误报） |
| 缓解闭环 | 零速(≤0.5km/h)+原因消失 → 手柄回零 → 按钮保持≥3s → IDLE |
| 自愈 | 限 1 次，超限转 FAULT 需人工/远程复位 |

### 8.3 EBR 硬线回路（ebr）：独立于网络的保命线

**得电=缓解、失电=制动**，串联常闭触点（手柄/ATP/紧急按钮）。为什么要有它？
因为 CAN 网络本身可能故障（Bus-Off、断线）——SIL4 执行路径必须独立于通信介质。
双回路 2oo2：任一失电即制动，单断线只预警不损失制动能力。

### 8.4 EB 执行反馈（exec_feedback）：决策 ≠ 执行

EBM 发请求只是决策，必须三重证据确认执行：制动缸压力 ≥300kPa + EB 激活回执 +
牵引切除联锁。任一缺失（2s 超时）判执行层故障；**APPLIED 期间牵引恢复 = 立即故障**
（边制动边牵引是最危险失效）。

### 8.5 CAN 错误状态机（errstate）：物理层健康

对标 ISO 11898-1：TEC/REC 错误计数 → Error-Active/Passive/Bus-Off 三态。
Bus-Off 后 128 次总线空闲恢复、恢复后 8 位发送退避。**接收错误不触发 Bus-Off**——
这条细节能区分"背过标准"和"读过标准"。

### 8.6 ATP 超速监督（atp）：三级干预

警告/SBI/EBI 三级阈值 + 动态 EBI 曲线（对标 ETCS 速度监督）：
距离目标点越近，允许速度线性收窄。

### 8.7 看门狗（watchdogs）：节点"还活着吗"

周期喂狗，连续 3 次丢失判离线，恢复需连续 2 次喂狗——防抖设计。

---

## 第 9 章 细节五：网络与时间——可复现、可追溯

### 9.1 虚拟时间基（timebase）：确定性

`VirtualClock`：virtual 模式下 `advance()/set()` 确定性推进，替代真实 `time.monotonic`。
**为什么重要**：测试要可复现，可复现要确定性时间。回放链/故障场景/看门狗全部
注入虚拟时钟——这是 HIL 平台的时间一致性基础设施。

### 9.2 故障生命周期台账（faultlife）：故障的"病历"

五阶段闭环：**注入 → 传播 → 告警 → 恢复 → 归档**。

```python
ledger = FaultLedger(clock, recorder)
ledger.open("overspeed", level="major", source="atp", detail="超速 170km/h")
ledger.alert("derate")
ledger.recover(); ledger.close()
ledger.report()    # total/open/closed/by_level
```

每个故障全程写入事件记录器（黑匣子）——**故障从发生到消失全程可审计**。

### 9.3 场景 DSL（faultlife + scenarios）：声明式测试

```python
scenario = FaultScenario("超速降级")
scenario.when("vcu", "overspeed", at=10.0, level="major", impact="速度超限", expect="derate")
scenario.expect_clear("overspeed", at=20.0)
report = ScenarioRunner(ledger, scenario, clock).run()
```

同样场景可以写成 YAML（scenarios/overspeed_derate.yaml），测试/演示人员无需改代码：

```yaml
name: 超速降级
steps:
  - at: 10.0
    inject: {node: vcu, fault: overspeed, level: major, impact: 速度超限, expect: derate}
  - at: 20.0
    recover: overspeed
```

### 9.4 完整回放链（replay）：真实数据驱动回归

`.asc` 日志 → 虚拟时钟 → 联锁/ATP/看门狗/EBM 全链路驱动 → 告警断言。
黑匣子语义闭环：**真实数据回放 + 安全逻辑裁决 = 事故复盘工具**。

---

## 第 10 章 细节六：验证层——测试怎么设计

### 10.1 测试金字塔在此项目的形态

| 层次 | 手段 | 例子 |
|------|------|------|
| 单元测试 | 逐模块行为断言 | test_ebm.py（8 原因×3 模式穷举） |
| 集成测试 | 模块协作 | test_replay.py（全链路） |
| 属性测试 | hypothesis 不变量 | 错误计数账目恒等、状态机合法性 |
| 故障测试 | 注入 + 断言 | 短路→全体 Bus-Off→恢复 |

### 10.2 覆盖率哲学

97.96% 是 `pytest-cov` 实测值，门禁在 `pyproject.toml`（`fail_under=97`，
CI 与本地共用单源）。未覆盖的部分是
刻意保留的防御性分支（QA 文档有逐行说明）。**诚实交代比硬凑 100% 更有说服力**。

### 10.3 跑测试

```bash
pip install -r requirements.txt
python run.py                    # 全量测试 + report.html
pytest tests/ --cov=tcms --cov-report=term-missing
```

---

## 第 11 章 证据链：从"测试"到"安全论证"（safety_case.md）

`docs/safety_case.md` 用 EN 50128 思路做安全论证映射：安全需求 SR-01~52 →
实现模块 → 测试证据 → 覆盖率证据。

**核心叙事**：SIL 不是"标上去的"，是**映射出来的**——每条安全需求都有实现、
每个实现都有测试、每个测试都有结果，形成证据链闭环。诚实声明：这是演示级论证，
非真实认证。

---

## 第 12 章 学习路线图（30 分钟 → 3 小时 → 一天）

### 30 分钟：跑通 + 看懂地图

```bash
python demo.py        # 一键全场景演示（纯文本）
python run.py         # 全量测试 + 报告
```

读 README 功能表 + 本文第 4 章地图，知道每个模块在链路中的位置。

### 3 小时：吃透安全逻辑层

按第 8 章顺序读 `interlocks → ebm → ebr → exec_feedback → errstate → atp → watchdogs`，
每个模块读 `tcms/*.py` 头部 docstring + 对应测试文件，跑单个测试文件：

```bash
pytest tests/test_ebm.py -q
```

### 一天：讲出完整故事

1. 能讲项目背景（第 1 章：安全攸关、故障难复现）与同类现状（第 2 章：CANoe 闭源贵、
   开源生态分散，本项目补空白）；
2. 能画全景图（第 4 章）并讲链路叙事；
3. 能讲 EBM 矩阵 + 双通道表决 + 缓解闭环（面试必问）；
4. 能讲 EBR 为什么独立于 CAN、执行反馈为什么需要三重证据；
5. 能讲错误状态机 ISO 11898-1 细节；
6. 能讲虚拟时间基为什么让测试可复现；
7. 能演示故障场景 DSL（YAML）与回放链；
8. 能讲证据链闭环与诚实边界（演示级论证）。

> 面试冲刺：读 `docs/interview_guide.md`——60 秒 STAR 叙事、六层面试话术、
> 高频追问 Q&A（HR 级/技术级/诚实边界）、现场演示脚本与数字速查卡，
> 背熟即可完整回答面试问题。

---

## 附录 A：常见问题

**Q: 为什么不用 CANoe？**
A: CANoe 是成熟工具，但它不展示"我理解协议层在发生什么"。本项目把 CANoe 的核心能力
（统计、Trace、错误统计、负载率）逐一对标实现，每一步都有物理/协议依据——面试要证明
的是"知道工具背后在算什么"。

**Q: 这是真实列车协议吗？**
A: 不是。报文协议为模拟设计（`README` 说明），红线是**不还原真实车型协议（涉密）**。
但安全逻辑（联锁/EBM/EBR/错误状态机）对标真实列控系统设计。

**Q: 覆盖率为什么不是 100%？**
A: 97.96% 实测值 + 门禁。剩余为刻意保留的防御分支，QA 文档逐行说明。

**Q: 怎么接真实硬件？**
A: 环境变量切换接口（`TCMS_BUS_INTERFACE` 等），代码零改动；`hardware` marker
隔离真实硬件用例，CI 自动跳过。

---

*教程结束。现在你可以从头到尾讲一遍这个项目了。*
