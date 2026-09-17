# TCMS-CAN-Test 安全论证映射表（Safety Case）

> 对标 EN 50128（铁路应用——通信、信号和处理系统的软件）的**软件安全论证**思路，
> 把"安全需求 → 设计实现 → 测试证据 → 覆盖率"串成一条可追溯的证据链。
> 本文件解决"SIL 等级是怎么来的"——不是拍脑袋写等级，
> 而是从**需求 → 实现 → 证据**全链路映射出来；配套需求追溯矩阵
> `tests/rtm.csv`（SR-01~52 → 测试文件）与测试计划 `docs/test_plan.md`。

---

## 0. 一句话定位

| 项 | 内容 |
|---|---|
| 项目 | TCMS-CAN-Test：列车控制与管理系统 CAN 总线仿真与安全逻辑验证 |
| 证据基线 | 960 用例（pytest collect，2026-09-08 实测）· 97.96% 语句覆盖率（2651 stmts / 54 miss）· CI 全绿 |
| 论证方法 | 软件功能安全（EN 50128 / IEC 61508 思想）的需求-实现-证据三层映射 |
| 覆盖范围 | 紧急制动、联锁、超速防护（ATP）、看门狗、错误状态机、可调度性、回放链、故障字典/追溯 |

> 说明：本项目是**教学/演示级**安全逻辑仿真，SIL 等级用于**论证方法演示**，
> 不代表真实车载系统已通过第三方认证。真实认证需 SIL 完整开发流程
> （需求管理/独立验证/工具鉴定等），本表映射的是其**可复现的技术内核**。

---

## 1. 安全需求目录（SR——Safety Requirement）

| ID | 安全需求 | 来源/对标 | SIL 关联 |
|---|---|---|---|
| SR-01 | 任一 SIL4 紧急制动原因触发时必须制动（故障安全：宁可错杀） | EBM 模式×原因矩阵 | SIL4 |
| SR-02 | SIL2 紧急制动原因必须双通道一致才制动（防误报） | EBM 通道表决 | SIL2 |
| SR-03 | 紧急制动后必须满足"零速 + 原因消失 + 有效速度信号"才可缓解 | EBM 缓解闭环 | SIL4 |
| SR-04 | 列车移动时车门打开/故障即违规（故障门按未关处理） | 门-车联锁 | SIL4 |
| SR-05 | 超速达到 EBI 阈值必须触发紧急制动干预 | ATP 三级监督 | SIL4 |
| SR-06 | 超速达到 SBI 阈值必须触发常用制动干预（先于 EBI） | ATP 三级监督 | SIL3 |
| SR-07 | 节点心跳丢失超过 N 周期必须判为离线/故障 | 看门狗/心跳监督 | SIL3 |
| SR-08 | CAN 总线错误计数超限必须进入 Bus-Off 并退避 | ISO 11898-1 错误状态机 | SIL2 |
| SR-09 | 周期报文必须满足可调度性（WCRT ≤ 截止期） | Tindell WCRT 分析 | SIL2 |
| SR-10 | 紧急制动事件必须被记录并可冻结窗口（黑匣子） | 事件记录器 | SIL2 |
| SR-11 | 故障必须经历"注入→传播→告警→恢复→归档"全生命周期留痕 | 故障生命周期台账 | SIL2 |
| SR-12 | 真实 CAN 日志回放必须驱动业务逻辑产生一致的响应 | 完整回放链 | SIL3 |
| SR-13 | 运行中禁止自动解除紧急制动（自愈限 1 次，超限转 FAULT） | EBM 自愈策略 | SIL4 |
| SR-14 | 牵引与制动不得同时施加（互锁） | 牵引-制动联锁 | SIL4 |
| SR-15 | 时间一致性：全部模块共享统一时间源（虚拟时间基） | 仿真基础设施 | SIL2 |
| SR-16 | 故障必须注册进统一故障字典（ID/级别/处置/SIL/检测/注入手段）且与分级模型对齐 | 故障字典 FMEA | SIL2 |
| SR-17 | 每条安全需求必须有可追溯的测试证据（需求→测试文件双向覆盖） | 需求追溯矩阵 RTM | SIL3 |
| SR-18 | 核心安全路径必须可独立快速回归（冒烟层）且失败自动留存现场 | 测试分层/失败导出 | SIL3 |
| SR-19 | 速度信号无效/丢失时速度监督必须降级，不得按 0 速误判 | ATP 速度监督 | SIL3 |
| SR-20 | 速度传感器冗余不足（2oo3 单通道）时必须降级监督 | ATP 表决 | SIL3 |
| SR-21 | 后车门（Door5-8）故障必须按未关处理，禁止发车 | 门-车联锁（后门） | SIL4 |
| SR-22 | 门气源压力低时必须切除该侧门并降级，不得误发"全部门关" | 门气源监督 | SIL2 |
| SR-23 | 运行中检测到门开（联锁失效场景）必须立即紧急制动 | 门-车联锁 | SIL4 |
| SR-24 | 牵引变流器故障（故障码非 0）必须封锁牵引并降级 | 变流器控制 | SIL2 |
| SR-25 | 变流器过温必须降功率运行，防止过热跳闸 | 变流器温度保护 | SIL2 |
| SR-26 | 直流母线欠压必须封锁牵引，防止欠压运行损坏 | 母线电压保护 | SIL2 |
| SR-27 | 制动缸压力泄漏（指令-反馈偏差）必须补偿制动降级 | 压力闭环监督 | SIL2 |
| SR-28 | 防滑系统故障必须告警，滑行保护降级 | WSP 监督 | SIL1 |
| SR-29 | 备用制动储压不足必须限速运行 | 备用制动监督 | SIL2 |
| SR-30 | 受电弓升弓失败（无确认）必须降级运行 | 受电弓控制 | SIL2 |
| SR-31 | 网压跌落必须告警并持续监控受流状态 | 网压监督 | SIL1 |
| SR-32 | 高压绝缘报警必须安全分断高压并停车 | 绝缘监测 | SIL3 |
| SR-33 | 电池单体电压不均衡必须告警并启动均衡 | BMS 均衡 | SIL1 |
| SR-34 | 充放电状态冲突必须能量管理降级 | BMS 状态机 | SIL2 |
| SR-35 | 辅助变流器故障必须低压负载降级 | 辅助供电 | SIL2 |
| SR-36 | 辅助电压越限必须负载降级保护 | 辅助供电 | SIL2 |
| SR-37 | 辅助变流器过载必须预警 | 辅助供电 | SIL1 |
| SR-38 | 辅助接触器粘连（分断无响应）必须安全分断 | 辅助供电 | SIL3 |
| SR-39 | 空调压缩机过流保护时必须制冷降级 | 空调控制 | SIL2 |
| SR-40 | 滤网堵塞/加热故障/新风风门卡滞必须告警（舒适度降级） | 空调监控 | SIL0-1 |
| SR-41 | PIS 显示屏降级/紧急对讲失效必须告警 | 乘客信息系统 | SIL2 |
| SR-42 | PIS 播报失步必须记录待数据同步 | 乘客信息系统 | SIL0 |
| SR-43 | 烟火探测器故障/灭火装置未就绪必须告警 | 烟火安全 | SIL3 |
| SR-44 | 多区烟火报警必须立即停车疏散 | 烟火安全 | SIL4 |
| SR-45 | 轴温过高必须限速运行至最近站 | 走行部监测 | SIL2 |
| SR-46 | 走行部监测传感器失效必须告警 | 走行部监测 | SIL1 |
| SR-47 | 客室照明支路故障必须告警 | 照明系统 | SIL0 |
| SR-48 | 应急照明未投入必须告警（疏散安全） | 照明系统 | SIL2 |
| SR-49 | 网关网段/冗余链路丢失必须降级 | 列车网络 | SIL2 |
| SR-50 | 报文周期抖动必须告警（实时性劣化） | 列车网络 | SIL1 |
| SR-51 | 司控台指令失效/VCU 看门狗超时必须安全降级或复位 | 列车控制 | SIL2 |
| SR-52 | 传感器采集值超物理量程必须告警 | 信号采集 | SIL1 |

---

## 2. 需求 → 实现映射（SR → 模块 → 关键设计）

| SR | 实现模块 | 关键设计点（面试讲） |
|---|---|---|
| SR-01/03/13 | `tcms/ebm.py` | 模式×原因矩阵；SIL4 任一触发即制动；缓解需零速+有效速度；自愈限 1 次 |
| SR-02 | `tcms/ebm.py` | `channel_vote()`：SIL2 双通道一致才制动，不一致累计 `vote_mismatches` |
| SR-04 | `tcms/interlocks.py` | `door_motion_conflict()`：移动+开门/门故障 → 违规，显式返回原因 |
| SR-05/06 | `tcms/atp.py` | Warning < SBI < EBI 三级阈值；`DynamicEbiCurve` 目标点限速模型 |
| SR-07 | `tcms/watchdogs.py` / `tcms/nmt.py` | 连续 N 周期丢失判 Fault，恢复需连续 M 次心跳；CANopen 心跳带状态语义 |
| SR-08 | `tcms/errstate.py` | TEC/REC 计数、Bus-Off 退避（ISO 11898-1） |
| SR-09 | `tcms/schedulability.py` / `tcms/busload.py` | Tindell WCRT 分析 + 位级帧模型负载率 |
| SR-10 | `tcms/recorder.py` | 环形缓冲 + 保护事件驻留 + `freeze_snapshot()` 事故冻结窗口 |
| SR-11 | `tcms/faultlife.py` | `FaultLedger` 五阶段台账（注入→传播→告警→恢复→归档），与 recorder 打通 |
| SR-12 | `tcms/replay.py` | `ReplayChain`：.asc → 虚拟时钟 → 联锁/ATP/看门狗/EBM → 告警断言 |
| SR-14 | `tcms/interlocks.py` | `traction_brake_conflict()`：牵引请求 + 制动请求 → 冲突 |
| SR-15 | `tcms/timebase.py` | `VirtualClock` 统一时间源，`advance()/set()` 确定性推进 |
| SR-16 | `tcms/faultdb.py` + `tcms/faults.yaml` | 203 条 F-TCMS 字典（含对齐校验，防 faultlevel 双源漂移） |
| SR-17 | `tests/rtm.csv` + `tests/test_rtm.py` | SR→模块→测试文件→用例追溯，test_rtm 自证完整性 |
| SR-18 | `pyproject` markers + `tests/conftest.py` | smoke/safety 分层 + 失败现场自动导出 hook |
| SR-19/20 | `scenarios/speed_signal_loss_derate.yaml` + `scenarios/signal_redundancy_loss_derate.yaml` | 速度信号失效/冗余不足 → 监督降级（场景注册表执行断言处置） |
| SR-21/22/23 | `scenarios/rear_door_fault_interlock.yaml` + `door_air_pressure_low_derate.yaml` + `door_open_moving_eb.yaml` | 后门按未关 / 气源低切除 / 运行中门开 EB |
| SR-24/25/26 | `scenarios/traction_converter_*` + `dc_link_undervoltage_block.yaml` | 变流器故障/过温/母线欠压 → 封锁牵引降级 |
| SR-27/28/29 | `scenarios/brake_cylinder_leak_derate.yaml` + `brake_wsp_fault_warning.yaml` + `brake_reserve_low_derate.yaml` | 泄漏/防滑/备用制动低压降级 |
| SR-30/31 | `scenarios/pantograph_fail_raise_derate.yaml` + `line_voltage_sag_warning.yaml` | 升弓失败降级 / 网压跌落告警 |
| SR-32/33/34 | `scenarios/battery_insulation_shutdown.yaml` + `battery_voltage_imbalance_warning.yaml` + `bms_charge_conflict_derate.yaml` | 绝缘分断 / 单体不均衡 / 充放电冲突 |
| SR-35/36/37/38 | `scenarios/aux_converter_*.yaml` + `aux_contactor_weld_shutdown.yaml` | 辅助供电故障族（降级/告警/安全分断） |
| SR-39/40 | `scenarios/hvac_compressor_overcurrent_derate.yaml` + `hvac_comfort_degradation_warning.yaml` | 空调过流降级 / 舒适度告警族 |
| SR-41/42 | `scenarios/pis_display_intercom_warning.yaml` + `pis_announce_desync_info.yaml` | PIS 显示/对讲/播报 |
| SR-43/44 | `scenarios/fire_detector_extinguisher_warning.yaml` + `fire_multizone_shutdown.yaml` | 烟火探测/灭火告警与多区停车 |
| SR-45/46 | `scenarios/bogie_axle_overheat_derate.yaml` + `bogie_sensor_fault_warning.yaml` | 轴温限速 / 走行部传感器告警 |
| SR-47/48 | `scenarios/light_group_fault_warning.yaml` + `emergency_light_fail_warning.yaml` | 照明支路 / 应急照明告警 |
| SR-49/50 | `scenarios/gateway_segment_redundancy_loss.yaml` + `frame_period_jitter_warning.yaml` | 网关冗余/周期抖动 |
| SR-51 | `scenarios/driver_console_vcu_watchdog.yaml` | 司控台失效/看门狗复位 |
| SR-52 | `scenarios/sensor_out_of_range_warning.yaml` | 传感器超范围告警 |

---

## 3. 实现 → 测试证据映射（模块 → 测试文件 → 用例数）

> 用例数为 `pytest --collect-only` 实测（2026-09-08 基线，960 collected =
> 959 passed + 1 hardware skip，44 个测试文件）。

| 模块 | 测试文件 | 用例数 | 覆盖的关键安全行为 |
|---|---|---|---|
| ebm.py | test_ebm.py | 78 | 模式×原因矩阵、缓解/复位闭环、司机缓解序列、通道表决 |
| recorder.py | test_recorder.py | 45 | 环形缓冲、保护事件、冻结窗口、导出、EBM 快照 |
| interlocks.py | test_interlocks.py | 51 | 门-车联锁、超速判定、牵引制动互锁、方向-速度联动 |
| properties.py | test_properties.py | 31 | 属性测试（不变量：计数器账目/状态机合法性/联锁⟺原因） |
| network.py | test_network.py | 30 | 多网段拓扑、异步缓冲网关（时延/溢出丢弃/足迹防环）、级联转发、审计日志 |
| exec_feedback.py | test_exec_feedback.py | 22 | 压力+回执+牵引切除三重证据、故障态粘滞 |
| atp.py | test_atp.py | 21 | 三级监督、动态 EBI 曲线、制动点计算 |
| nmt.py | test_nmt.py | 21 | 心跳生产者/消费者、NMT 主站命令、状态迁移 |
| voting.py | test_voting.py | 21 | 2oo3→2oo2 降级、单通道故障诊断 |
| faultlevel.py | test_faultlevel.py | 20 | 四级分级、处置映射、注入编排 |
| faultlife.py | test_faultlife.py | 21 | 五阶段台账、多故障审计、场景 DSL 断言、未知故障兜底 |
| **faultdb.py** | **test_faultdb.py** | **20** | **故障字典 FMEA：字段校验/唯一性/级别-SIL-处置对齐/查询 API** |
| busload.py | test_busload.py | 19 | 位级帧模型负载率、压测 |
| ebr.py | test_ebr.py | 19 | 硬线回路失电制动、断线诊断 |
| bypass.py | test_bypass.py | 18 | 隔离开关、强制 RM、审计日志 |
| scenarios.py | test_scenarios.py | 17 | YAML 场景加载/执行、显式/事件式语法、错误处理 |
| schedulability.py | test_schedulability.py | 17 | WCRT 分析、ID 审计 |
| timebase.py | test_timebase.py | 17 | 虚拟时钟推进/跳变、全局替换 |
| errstate.py | test_errstate.py | 34 | TEC/REC 迁移、Bus-Off 退避 |
| simulator.py | test_simulator.py | 15 | DBC 周期发送、状态注入 |
| parser.py | test_fault_injection.py | 15 | 采集/统计/丢报、错误注入 |
| faults.py | test_faults.py | 14 | CRC-8、位翻转 |
| protocol.py | test_protocol.py | 13 | 报文常量、编码 |
| jitter.py | test_jitter.py | 12 | ppm 漂移、迟到事件 |
| seqcheck.py | test_seqcheck.py | 12 | 丢/重/乱/迟检测 |
| replay.py | test_replay.py | 12 | 回放链、超速/开门触发 EBM、看门狗离线告警 |
| watchdogs.py | test_watchdogs.py | 11 | 心跳丢失判 Fault、恢复阈值、与仿真器集成 |
| canlog.py | test_canlog.py | 11 | .asc 解析、回放、统计 |
| busfault.py | test_busfault.py | 11 | 短路断路集体 Bus-Off |
| **reporting.py** | **test_reporting.py** | **11** | **JUnit 趋势聚合：解析容错/排序/渲染/历史报表** |
| bus.py | test_bus.py | 8 | 硬件接口抽象（1 hardware skip） |
| **badges 自证** | **test_badges.py** | **9** | **README 徽章自证：JUnit/coverage 解析/渲染/就地改写 + 失败红徽章（防手抄漂移）** |
| lifecycle.py | test_lifecycle.py | 7 | 生命周期 |
| **scenarios registry** | **test_scenario_registry.py** | **211** | **104 个 YAML 场景端到端闭环 + 故障键在字典内 + 场景库规模守卫 + 无孤儿故障不变量** |
| multinode.py | test_multinode.py | 6 | 多节点失活恢复 |
| fault_chain | test_fault_chain.py | 6 | 端到端故障链（burst → WCRT → 看门狗 → EBM） |
| **RTM 追溯** | **test_rtm.py** | **6** | **rtm.csv 完整性：SR 全覆盖/无重复/状态合法（元测试）** |
| fuzz.py | test_fuzz.py | 5 | 模糊测试 |
| **examples/** | **test_examples.py** | **2** | **.asc 样例可解析 + replay_demo 剧情断言可复现** |
| **失败导出 hook** | **test_failure_export.py** | **2** | **失败现场自动导出 summary/json/csv（元测试）** |

合计 **960 用例（44 文件）**。

---

## 4. 覆盖率证据（pytest-cov 实测，2026-09-02）

| 指标 | 值 |
|---|---|
| TOTAL 语句 | 2651 |
| 未覆盖 | 54 |
| 语句覆盖率 | **97.96%** |
| 覆盖率门禁 | pyproject `fail_under=97`（CI 与本地共用单源） |
| 全绿基线 | 960 用例 · CI run 全绿（pr-smoke + lint + test 3.10/3.11/3.12/3.13 + demo-smoke + dist-smoke） |

> 术语说明：pytest-cov 度量的是**语句覆盖率**（statement coverage，`stmts`），
> 即 `coverage.py` 的 line coverage 口径，不是分支/MC/DC 覆盖（见 §6）。
> 语句数为**本次新增用例后的实测**（2026-09-07）；README 徽章由 CI
> `scripts/gen_badges.py` 依据 coverage.json 自动刷新，本表为文档快照。

---

## 5. 证据链闭环示意（面试一页讲法）

```
安全需求 (SR-01~SR-52)
    │  需求驱动（rtm.csv 双向追溯）
    ▼
设计实现 (ebm/atp/interlocks/watchdogs/recorder/faultlife/replay/faultdb…)
    │  每模块配专项测试
    ▼
测试证据 (44 测试文件 / 960 用例)
    │  pytest-cov 度量 + 冒烟层快速门禁
    ▼
覆盖率门禁 (97.96% > 97% 门槛)
    │  CI：pr-smoke + lint + test 矩阵(3.10/3.11/3.12/3.13) + demo-smoke
    ▼
可追溯报告 (本表 + rtm.csv + 事件记录器导出 + 回放链报告 + 故障台账 + 失败现场)
```

**面试叙事**："我的 SIL 不是标上去的，是映射出来的——每条安全需求（SR）
都有实现模块、有专项测试、有覆盖率证据，还有 rtm.csv 双向追溯矩阵保证
需求与测试不脱节，最后 CI 门禁保证任何一次回归都过不了 97% 的门槛。
这就是 EN 50128 证据链的技术内核。"

---

## 6. 局限与说明（诚实性加分项）

1. **SIL 等级为论证演示**：真实 SIL 认证需要完整生命周期（独立安全评审、
   工具鉴定、需求变更管理等），本项目聚焦**可复现的技术内核**。
2. **覆盖率为语句覆盖**：语句覆盖 ≠ 分支/MC/DC 覆盖（EN 50128 对 SIL3/4 要求
   更强的结构覆盖）。本项目以语句覆盖 + 属性测试 + 模糊测试补充。
3. **SIL 自证循环的边界**：SR 表 → 模块 → 测试的映射是**仓库内自证**
   （测试断言与设计文档同源），真实认证要求独立第三方验证与评审记录；
   本表的价值在**论证方法示范**与可审计的追溯链，不在认证效力。
4. **硬件层留白**：真实 CAN 硬件联调（PCAN/周立功）为 `@pytest.mark.hardware`
   标记用例，CI 跳过——接入硬件后证据链补上物理层环节。
5. **时间基**：虚拟时间基解决仿真确定性，但真实时钟的漂移/抖动行为
   需要硬件在环才能完整验证。
