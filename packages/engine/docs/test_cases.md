# 测试用例设计详解（Test Cases）

> 承接 README 的"测试用例设计"一节：按测试工程师工作流逐文件详解 960 个用例
> 的设计思路（含面试讲解素材）。测试分层策略与出入口准则见
> [docs/test_plan.md](test_plan.md)，深度设计见 [docs/features.md](features.md)。

## 逐测试文件详解

**协议静态验证（`test_protocol.py`）**：DBC 结构完整性、报文 ID 唯一性与标准帧约束、
DLC、周期属性（50/100/500ms）、报警事件型配置、信号物理值域（车速 0-200km/h、
SOC 0-100%、温度 -40~120℃）、枚举值表、节点表。

**仿真器行为（`test_simulator.py`）**：7 个周期报文在总线上齐发、心跳/手柄周期实测、
心跳计数器单调递增、车速设置-解码往返一致、牵引/制动联动逻辑、车门状态与
AllDoorsClosed/开门许可联动、报警事件触发与解码、丢报注入。

**故障注入与边界值（`test_fault_injection.py`）**：车速/手柄/SOC/温度边界值编码与
越界拒绝、超速→报警事件联动（>160km/h）、心跳丢失检测、单报文丢失不影响其他报文、
时钟抖动下计数器完整性、车门故障安全（AllDoorsClosed 不得置位）、原始帧往返无损、
未知报文 ID 拒绝解码。

**安全联锁逻辑（`test_interlocks.py`）**：门-车联锁（开门/故障状态下移动即违规）、
超速判定边界（160km/h 含等于不触发）、紧急制动决策矩阵、受电弓拉弧风险
（电压异常 + 弓升）、SOC 能源联锁。

**多节点总线（`test_multinode.py`）**：节点-报文归属映射完整性、节点级失活隔离
（BMS 失活仅影响能源报文）、BCU 失活隔离、节点恢复后报文重新出现、未知节点拒绝。

**紧急制动管理（`test_ebm.py`）**：模式×原因矩阵穷举（8 原因 × FAM/CM/RM 3 模式）、
触发→缓解→复位闭环、司机缓解操作序列（手柄回零 + 缓解按钮保持 ≥3s）、自愈复位限次与
远程复位、降级链模式迁移（FAM→CM→RM 单步合法、跳级拒绝）、SIL2/SIL4 双通道表决、
非法输入健壮性。

**EBR 硬线回路（`test_ebr.py`）**：触点开路→失电→制动（fail-safe 方向）、断线 vs 请求源
诊断推理、diag_pulse 自检、双回路 2oo2（任一失电即制动、单断线降级不损失制动能力）、
未知触点/同一实例拒绝。

**EB 执行反馈（`test_exec_feedback.py`）**：三重证据（压力+回执+牵引切除）齐备才确认
执行、证据到达顺序无关、压力阈值边界、反馈超时判执行层故障（缺项明细）、
APPLIED 中牵引恢复→联锁违背立即故障、缓解压力回落、时间单调性（乱序/重放防护）、
故障态粘滞与维护复位。

**CAN 错误状态机（`test_errstate.py`）**：TEC/REC 计数规则（+8/-1/被动跳变 120/119）、
Error-Passive 阈值迁移（128）、Bus-Off 触发（TEC≥256）与离线隔离、128 次总线空闲恢复、
计数器 8 位封顶、错误类型统计、状态迁移回调、混合错误-成功震荡、软件复位。

**事件时序记录器（`test_recorder.py`）**：环形缓冲容量与淘汰、类型/ID/方向/类别/时间段/
关键词过滤查询、统计聚合、JSON/CSV 导出（UTF-8 无 BOM）、RecordedBus 收发帧透明记录、
EBM/错误状态机 hook 接线、统一时间线完整性与可序列化。

**总线负载率（`test_busload.py`）**：位级帧模型锚点（0 字节=55 位、8 字节=135 位最坏值）、
滑动窗口负载率收敛、设计上限/预警线评估、背景流量规划（30/60/90% 目标）、
高负载→低优先级帧劣化机制。

**可调度性（`test_schedulability.py`）**：WCRT 无干扰=R=C、高优先级干扰迭代、
饱和导致不可调度、抖动放大干扰、真实 DBC 报文集全可调度、利用率口径、
ID 分配审计（安全报文须占最低 ID 段，识别 BrakeSystem 0x700 风险点）。

**硬件接口层（`test_bus.py`）**：环境变量配置读取、virtual 总线创建、覆盖项透传、
非法位速率拒绝、`hardware` marker 隔离（CI 自动跳过）。

**属性测试（`test_properties.py`，hypothesis）**：以不变量验证替代逐例验证——
错误计数器范围与账目恒等、Bus-Off 隔离与恢复归零、状态-计数一致性、
EBM 触发-适用性-动作逐项吻合、任意操作序列后模式/状态合法、联锁违规⟺原因非空、
阈值单调、EBR fail-safe 与诊断一致、双回路 2oo2 降级、执行反馈状态机合法性与
故障态粘滞、证据完备才确认、环形缓冲容量上限与查询只读性。

**总线级故障注入（`test_busfault.py`）**：短路/断路 → 全体节点集体 Bus-Off（共享介质
故障影响所有节点）、恢复后全部回 Error-Active、干扰 → REC 上升不触发 Bus-Off、
故障期间禁止重复注入、部分空闲恢复保持 Bus-Off（128 位达标才恢复）。

**时序质量（`test_jitter.py` + `test_seqcheck.py`）**：帧间隔 min/max/mean/σ 统计、
迟到事件计数与容忍边界、ppm 漂移（慢/快时钟 ±1000ppm）与 200ppm 告警阈值、
时间戳回退拒绝；报文序列检测——乱序（跳号/回退）、重复帧（同序号短间隔）、
迟到帧（超容忍）、丢帧（超时）、多 ID 序列隔离、计数器回绕合法。

**故障分级模型（`test_faultlevel.py`）**：四级故障（info/minor/major/critical）分类、
模式敏感处置（critical 任何模式紧急制动、major 在 RM 下仅告警）、
处置优先级合并、注入编排器（叠加/升级/清除/影响评估报告）。

**ATP 超速监督（`test_atp.py`）**：警告/SBI/EBI 三级阈值边界（等于不触发、超限即 EBI）、
速度无效不监督、自定义阈值；动态 EBI 曲线——目标点限速线性逼近、允许速度
单调且封顶当前速度、制动触发点反解、参数校验。

**CANopen NMT 心跳（`test_nmt.py`）**：生产者 boot-up + 状态字节、COB-ID（0x700+node_id）、
node_id 边界校验、状态迁移；消费者 3 周期超时判心跳丢失、boot-up 重置计时、
复位语义；生产者→消费者闭环集成。

**2oo3 速度表决（`test_voting.py`）**：三通道一致/多数一致取均值、容差边界、
发散判定、单通道故障忽略、双通道故障表决器失效、故障清除恢复、自定义容差。

**虚拟时间基（`test_timebase.py`）**：`VirtualClock` 默认 monotonic 模式、
virtual 模式 advance/set 确定性推进、负值拒绝、virtual-only 限制、模式切换、
全局单例替换（仿真全链路统一时间源）。

**故障生命周期台账（`test_faultlife.py`）**：五阶段迁移（注入→传播→告警→
恢复→归档）、同名幂等复用、未开账/已归档拒绝、recorder 事件联动、审计报告
（total/open/closed/by_level）、场景 DSL——`when(节点, 故障, at=时刻, expect=动作)`
声明式故障场景 + `expect_clear` 恢复断言，`ScenarioRunner` 按时间序执行并输出
断言报告。

**完整回放链（`test_replay.py`）**：`.asc` 帧 → 虚拟时钟 → 联锁/ATP/看门狗/EBM
全链路驱动——正常行驶无告警、超速(>160)→EBI 触发 EBM+ATP 告警、门开移动触发
联锁告警、心跳丢失→看门狗 fault、recorder 联动、报告结构完整（帧数/告警分类/
EBM 状态/看门狗状态/ATP 级别）。

**场景 YAML 外部化（`test_scenarios.py`）**：`parse_scenario`/`load_scenario`/
`run_yaml`/`run_scenarios` 一键加载执行；YAML 语法与 `FaultScenario.when()/
expect_clear()` 一一对应（显式 `inject/recover` 与事件式 `action` 两种写法）、
缺 at/未知动作/空 YAML/无 steps/目录不存在均报错；`scenarios/*.yaml` 示例：
超速降级（inject major→expect derate→recover）、EB 失效（critical→expect
emergency_brake）、门故障级联（事件式写法）。

**多网段拓扑（`test_network.py`，30 用例）**：`BusNetwork` 多段构建（空拓扑拒绝）、
网关白名单（只转发允许 ID，空=全转发）/黑名单（拦截名单 ID）、无网关网段隔离、
**异步缓冲转发**（send 只入缓冲，时钟推进+`step()` 泵出，转发时延可测）、
**缓冲溢出丢弃新帧**（满则不阻塞发送方）、级联扩散（propulsion→brake→doors，
逐网关两拍到达）、**足迹防环**（帧不重复过同一网关，双向网关回环一次即止）、
转发统计/溢出计数/缓冲占用与审计日志深拷贝、热插拔段、`recv_any` 跨段统一
接收（阻塞轮询全段）、发送/转发失败容错（can.CanError → 返回 False /
日志 forwarded=False）、负时延/零容量拒绝、未知段拒绝。

**FMEA 故障字典（`test_faultdb.py`，20 用例）**：`tcms/faults.yaml` 203 条
F-TCMS 故障条目逐条字段校验（11 必填字段）、fid/key 唯一性、级别/处置/SIL/
层级合法性、与 `faultlevel.FAULTS` 同名条目**级别一致性**（防双源漂移）、
按 key/fid/级别/子系统/SIL/层级查询、字典自检报告——真实测试工程师"先统一
故障口径再设计用例"的第一步。

**RTM 需求追溯（`test_rtm.py`，6 用例 + `rtm.csv`）**：SR-01~52 → 模块 →
测试文件 → 验证用例的双向追溯矩阵；`test_rtm` 是元测试——自证 CSV 可解析、
SR 全覆盖、无重复条目、状态合法。追溯完整性本身被测试锁定。

**YAML 场景注册表闭环（`test_scenario_registry.py`，121 用例）**：`scenarios/*.yaml`
104 个场景逐个端到端执行断言全部通过，且场景引用的故障键必须存在于故障字典、
场景库规模受守卫约束（≥5）——**场景 ↔ 字典耦合被测试锁定**，字典改名立刻红灯。

**失败现场自动导出（`test_failure_export.py` + conftest hook）**：用例失败时若
注册了 `crash_site`（recorder/台账等），自动导出 `reports/failures/<用例>/`
下 summary + JSON/CSV 时间线——**失败的测试自动留下现场证据**，无需复跑。

**趋势报表（`test_reporting.py`，11 用例）**：JUnit XML 解析（单/多 suite、坏文件
容错、非 testsuite 根忽略、时间戳缺省回退）、历史聚合排序、Markdown/ASCII 渲染。

**examples 示例回归（`test_examples.py`，2 用例）**：`examples/demo_trip.asc`
可解析且含心跳/车速/门三类报文；`replay_demo.py` 子进程运行 5 步剧情断言全过——
**文档示例也进回归**，改坏即红灯。
