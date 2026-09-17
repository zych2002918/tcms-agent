# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。
所有重要变更记录于此；格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.12.0] - 2026-09-08

### 新增（Q2-P-A 第二增量 Wave A+B+C：FMEA 66→202 / 场景 59→103 —— 达到 P-A 200+/100+ 门槛）

- **FMEA +31 条**（13 域再深一层）：双端驾驶模式冲突/司控台通讯丢失/停放制动未缓解/牵引切除失效/动态限速曲线异常/车门无法关闭/开门侧不一致/灭火压力低/抗蛇行减振器失效/速度通道 A 失效等
- **场景 +16 文件**（域内多故障编排，75 场景全部可执行、无孤儿故障不变量保持）
- **Wave B（97→135 / 75→89）**：+38 FMEA（运营/检修/保护类再深化：EBR 自检/空压机/再生制动失效/直流接地/门锁机构/运行中降弓/深度放电/辅逆输出/制冷剂不足/CCTV/火警面板/二系悬挂/速度脉冲噪声等）；+14 场景（域内链式编排）
- **Wave C（135→202 / 89→103）**：+67 FMEA 至 **202 条（达 200+ 门槛）**（RAM 奇偶/编组配置/NMT 生命保持/发车前 EB 测试/轮径标定/电机绕组过温/三相不平衡/紧急解锁/门机防夹失控/滑板磨耗/网压谐波/合闸回检/辅逆输出过流/蒸发器结霜/客流计数/CCTV 摄像头（上波）/灭火释放抑制/踏面擦伤/齿轮箱油压/速度脉冲间歇/模拟地噪声等）；+14 场景至 **103（达 100+ 门槛）**；多网段与四向追溯见后续
- 用例 870 → **958 collected（957 passed + 1 skipped）**；覆盖率口径不变
- **③-c 多网段**：DBC GenMsgSegment（vehicle 13/comfort 7/backbone 1）单一真源 + schedulability 网段级分析（vehicle 9.4% / comfort 1.1% / backbone 0.1%，全可调度）

### 变更

- 计数文档同步（README/features/interview_guide/safety_case/test_cases/test_plan/tutorial）

### 发布态补记（本次打 tag 时的实测口径）

本版打 tag 前又合入两笔资产合规提交（`door_fault` 运行态 / `overspeed` ATP 分级
处置取决于条件，补 `action_note`），故 **v1.12.0 实际发布态**为：

- 用例 **960 collected = 959 passed + 1 hardware skip**（44 测试文件）
- 覆盖率 **97.96%**（2651 stmts / 54 miss，门禁 97%）
- FMEA **203** 条 / 场景 **104** 个 / DBC **22 报文 · 116 信号 · 11 节点** / SR-01~52
- 另修：CHANGELOG 重复标题块移除（文首 `# Changelog` 曾出现两次，破坏章节解析）

## [1.11.0] - 2026-09-08

### 新增（Q2-P-A 资产真实化第一增量）

- **DBC 报文库 8 → 22 报文 / 信号 38 → 116**（+14 帧）：覆盖 HVAC/PIS/照明/烟火/
  辅助变流/ATO/走行部/后门/网关/防滑/牵引变流/司控台/充电机 13 域，11 个发送节点，
  周期分级 25/50/100/250/500ms（扩展帧名义值按 DBC 周期纳入仿真）
- **FMEA 故障字典 22 → 66 条**：13 系统域 / 16 子系统，故障条目覆盖真实化增量域
- **场景库 25 → 59**（+34）：剧本族组织，既有"无孤儿故障"不变量保持
  （每个 FMEA 键至少被一个场景消费）
- **安全需求 SR-01~18 → SR-01~52**：RTM 追溯锚定场景注册表（52 需求映射）

### 变更

- 用例数 802 → **870**（collected，869 passed + 1 hardware skip），覆盖率 **98.00%**
  （2611 语句 / 53 未覆盖），测试文件 44 → 44
- `simulator` 现按 DBC 周期发送全部周期报文（含 13 域扩展帧名义值）
- `test_protocol` / `test_schedulability` 断言改为扩库后口径
- 单总线 250 kbit/s 利用率约 **10.5%**，全部报文可调度

## [1.10.0] - 2026-09-07

### 新增（真实运营场景库扩充：13 → 25 事件式场景 / FMEA 22 → 26 故障）

- **场景库 13 → 25**（+12 个，全部贴合实际列车运行）：
  - +6 真实运营场景：模式变体 `eb_failure_mode_variant` / 三故障级联
    `heartbeat_traction_overspeed_triple` / 复合升级
    `turnaround_pantograph_bus_combo` / 恢复重发 `overspeed_reinject_repeat`
    / 实际时序 `timetable_multi_node_timing` / 运维组合 `soc_temp_door_noise`
  - +3 运营场景：受电弓拉弧降弓停车 `pantograph_arc_shutdown` / SOC 低
    充电建议 `soc_low_charge_guard` / 传感器漂移卡死致超速升级
    `sensor_fault_escalation_overspeed`
  - +3 场景（22 → 25）：HVAC 车厢过热降额 `hvac_cabin_overheat_derate` /
    烟雾检测停车 `smoke_detected_shutdown` / 走行部振动告警
    `bogie_vibration_warning`（+ 速度漂移超速升级 `speed_drift_overspeed_escalation`
    等，与下述 FMEA 扩量配套）
- **故障字典（FMEA）22 → 26**：AlarmEvent 增 HvacFault / BogieVibration 报警位，
  +4 真实列车故障，3 新域故障物理信号化（信号 36 → 38）
- **无孤儿故障不变量测试**：确保每个 FMEA 键至少被一个场景消费
  （场景 ↔ 故障字典全覆盖闭环）

### 变更

- 用例数 777 → **802**（collected，801 passed + 1 skipped），覆盖率 **98.00%**
  （2600 语句 / 52 未覆盖），测试文件 44 → 44
- README 徽章机器自证同步（`scripts/gen_badges.py`）

## [1.9.1] - 2026-09-03

### 修复（v1.9.0 发布后 CI 实证暴露，收尾包）
- **Python 3.10 兼容**：`test_metadata` 的 `tomllib`（3.11+ 标准库）回退
  `tomli`（pyproject [test] extra 条件声明）——v1.9.0 tag 的 wheel 装
  `[test]` 后在 3.10 跑 pytest 会收集失败
- **release.yml 收敛与加固**：去除 `--cov-fail-under=97` 双源（与
  ci.yml/pyproject 单源）；发布前 wheel 分发冒烟（干净 venv 实证
  import + 数据资产 + 入口，防"能 build 但装上残"的 Release 资产）
- **覆盖率口径稳定**：`tcms/cli.py __main__` 主块标 `# pragma: no cover`
  （入口样板，消除对主块的不稳定统计）

### 新增
- **第二消费者示例** `examples/consumer_api.py`：仅经 `tcms` 顶层公共
  API（load_database / make_bus / load_fault_dictionary /
  scenarios.run_yaml）完成 总线→仿真→场景→回放 全流程自证——平台化
  "外部使用者可写自己用例"的实证（smoke 测试 + CI demo-smoke 复跑）

### 变更
- 用例数 776 → **777**（+1：consumer_api），覆盖率 97.96% → **98.00%**
  （2600 语句 / 52 未覆盖，门禁 97%），测试文件 44 → 44
- README/docs 数字全量机器自证同步（777 / 98.00 / 44 测试文件）

## [1.9.0] - 2026-09-03

### 新增（平台化：可分发 × 公共契约 × 单源收敛）
- **包公共 API 面**：`tcms/__init__.py` 顶层导出 `__version__` /
  `load_database` / `load_fault_dictionary` / `make_bus` /
  `scenarios.run_yaml`（此前为空文件，`import tcms` 无任何可用入口）
- **CLI 收编为包内入口** `tcms/cli.py`：run.py 薄壳委托 + console script
  `tcms-test` 指向同一实现（此前指向仓库根 run.py，wheel 分发后必然
  ModuleNotFoundError）；README/CI/安装态命令现为单一真源
- **`tcms-test --doctor` / `python run.py --doctor` 环境自检**：
  依赖版本 / 包版本一致性 / DBC+FMEA 数据资产 / virtual 总线 / HIL 硬件状态
  / 场景目录 → PASS/FAIL 表（复用 python-can 探测；无硬件时显式 FAIL 并
  指引 TCMS_BUS_* 接入——Roadmap HIL 的探测工具就绪化）
- **版本单一真源**：`tcms/_version.py` + pyproject `dynamic version`
  （消除 pyproject/run.py 双写漂移）；`tests/test_metadata.py` 元测试
  钉死版本/打包数据/公共 API 契约
- **DBC 收编入包**：`dbc/tcms.dbc` → `tcms/tcms.dbc`，`protocol.py` 改
  `importlib.resources` 寻址（wheel/zip 安装均可加载，外部 DBC 仍可传路径）
- **分发自检** `scripts/check_dist.py` + CI `dist-smoke` job：wheel 安装于
  干净 venv 后从仓库外实证 `import tcms` + 数据资产 + 入口（堵住历史
  editable-only 盲区）
- **性能基准** `scripts/benchmark.py`：回放吞吐（frames/s）/ WCRT 整集分析
  （200 报文 ms）/ 总线负载滑动窗口（ms）三项机器产物，`--json` 落盘；
  CI demo-smoke 每轮生成 `reports/benchmark.json`（Roadmap"性能可追踪"勾选）
- **架构手册** `docs/ARCHITECTURE.md`：分层依赖方向 / 证据链数据流 /
  扩展点食谱（新报文/故障键/场景/HIL/接入方）/ 契约约定表
- **第二消费者示例** `examples/consumer_api.py`：仅经 `tcms` 顶层公共 API
  （load_database/make_bus/load_fault_dictionary/scenarios.run_yaml）完成
  总线→仿真→场景→回放全流程自证——平台化"外部使用者可写自己用例"的
  实证（CI demo-smoke + test_examples 子进程复跑）
- 测试分层新增：`tests/test_cli.py`（13）+ `tests/test_diagnose.py`（13）
  + `tests/test_metadata.py`（8）+ `tests/test_benchmark.py`（4），覆盖 CLI
  各分支、doctor 防御路径与基准脚本可运行性

### 变更
- 用例数 738 → **777**（+39：cli 13 / diagnose 13 / metadata 8 / benchmark 4 /
  consumer_api 1；776 passed + 1 hardware skipped），覆盖率 97.82% →
  **98.00%**（2600 语句 / 52 未覆盖，门禁 97%），测试文件 43 → 44
- 覆盖率门禁单源：CI（ci.yml + release.yml）移除 `--cov-fail-under=97`
  （只留 pyproject `[tool.coverage.report] fail_under=97`，消除阈值双源）
- `cli.py __main__` 主块标 `# pragma: no cover`（入口样板，消除覆盖率
  对主块的不稳定统计抖动）
- **Python 3.10 兼容修复**：test_metadata 的 `tomllib`（3.11+ 标准库）回退
  `tomli`（pyproject [test] extra 条件声明），CI test(3.10) 矩阵恢复全绿
- **Release 流程加固**：发布前 wheel 分发冒烟（干净 venv 实证 import+
  数据资产+入口），防"能 build 但装上残"的 Release 资产
- `requirements.txt` 继续 `-e .[test,viz,lint]`；ruff 全仓干净
- 版本入口统一：`run.py --version` 与 `tcms-test --version` 均输出 1.9.0

## [1.8.0] - 2026-09-02

### 新增
- `scenarios/` 扩充 8 → **13 个场景**：新增 场景9-13（总线噪声+仲裁错误+短帧 /
  VCU 心跳丢失+牵引丢失级联 / 制动卡滞+受电弓拉弧 / 牵引制动冲突 / SOC+温度+门噪声），
  22 条 FMEA 故障键全部被场景消费（注入/恢复/断言三件套覆盖字典全键）
- **Allure 结果 CI 化**：全量回归 `--alluredir` 产物按 Python 版本上传 artifact
  （下载后 `allure serve` 看板化）
- 场景头部注释 F-TCMS 编号与 `tcms/faults.yaml` 逐条对齐（消除引用错位）

### 变更
- 用例数 728 → **738**（+10：场景注册表 5 新 YAML × 2 参数化；737 passed + 1 skipped）
- 场景注册表测试 18 → 28 用例（13 YAML × 2 参数化 + 2 守卫）
- Roadmap 勾选：场景库扩充 / Allure CI / Python 3.13 / JUnit 趋势接入 Pages
  （前述 CI/Pages 能力在 v1.7.0 已落地，本版补全收尾）
- **RTM 追溯补齐 SR-16~18**：`tests/rtm.csv` + `tests/test_rtm.py` 原只覆盖 SR-01~15，
  与 `docs/safety_case.md` 定义的 18 条对齐（补 6 行：FMEA 字典/追溯自证/失败导出与分层）
- **README 精简**：504 → 148 行（详情迁至新 `docs/features.md` + `docs/test_cases.md`，
  消除"后续规划"与 Roadmap 重叠；ASCII 图改英文短标签防错位；数字同步 738/13 场景/SR-01~18）

## [1.7.0] - 2026-09-02

### 新增
- **FMEA 字典 → 场景引擎闭环**：`ScenarioRunner` 处置动作回退到统一故障字典
  （`faultdb`），`tcms/faults.yaml` 全部 22 条故障键现可被场景 YAML 消费
  （此前仅 faultlevel 10 键可用，扩展键注入即抛错）
- `scenarios/` 扩充至 **8 个场景**：新增 CRC 错误风暴 / 总线短路-断路级联 /
  节点重启风暴 / 传感器卡死+漂移叠加 / 滚动计数跳变（覆盖字典扩展故障键）
- **README 徽章自证**：`scripts/gen_badges.py` 从 JUnit + coverage.json 机器产物
  生成 tests/coverage 徽章并就地改写 README（保留元数据徽章；消除手抄漂移）；
  CI main 分支自动刷新并 bot 提交（`[skip ci]` 防递归）
- **Pages 实时报告**：`docs/reports/` 随 CI 生成 `latest.json`（tests/coverage/
  python 版本）+ TREND.md + report.html，文档站统计数字由 JSON 动态驱动
- CI test 矩阵扩至 **Python 3.10/3.11/3.12/3.13**；GitHub Actions 版本全家桶
  升级（checkout@v7 / setup-python@v7 / upload-artifact@v7 / configure-pages@v6 /
  action-gh-release@v3），dependabot 待开 PR 全部消除
- `tests/test_badges.py`（8 用例）+ 场景库规模守卫（≥5）

### 变更
- 用例数 707 → **726**（+19：badges 8、场景注册表 +11、守卫），测试文件 37 → 40
- 覆盖率 97.81% → **97.73%**（2426 语句 / 55 未覆盖，门禁 97% 达成）
- smoke 层 67 → 68 用例（实测收集）
- JUnit 产物版本化命名（`reports/junit-py<ver>.xml`），支持多版本趋势聚合

## [1.6.0] - 2026-09-02

### 新增（测试工程师工作流完整化）
- `tcms/faultdb.py` + `tcms/faults.yaml`：**统一故障字典（FMEA）**——22 条 F-TCMS
  条目（fid/key/子系统/层级/级别/处置/SIL/检测/注入/恢复/描述），查询 API 与
  faultlevel 分级模型**对齐校验**（防双源漂移）
- `tests/rtm.csv` + `tests/test_rtm.py`：**需求追溯矩阵**（SR-01~18 → 模块 →
  测试文件双向追溯，元测试锁定完整性）；`docs/test_plan.md` 测试计划
- 测试分层：`smoke`（67 用例 ~1s）/`safety`（70）marker + `run.py --level` +
  CI `pr-smoke` 快速门禁 job
- **失败现场自动导出**：conftest `crash_site` fixture + hook →
  `reports/failures/<用例>/`（summary + recorder JSON/CSV）
- `tcms/reporting.py` + `scripts/report_history.py`：**JUnit 趋势报表**
  （Markdown/ASCII，CI 每轮落盘）
- `examples/`：`demo_trip.asc`（146 帧真实格式日志）+ `replay_demo.py`
  （5 步剧情断言）+ `make_demo_asc.py` + README；`demo.py` 全场景 **25 项自证断言**
- CI 重构：`pr-smoke`（smoke 先行）→ `lint`（含 ruff format）→ `test` 矩阵
  （3.10/3.11/3.12，JUnit + HTML artifact）→ `demo-smoke`（demo/示例/趋势）
- `.github/dependabot.yml`（pip + GitHub Actions 每周）

### 变更
- **版本口径统一**：pyproject 1.4.0 → **1.6.0**（此前 CHANGELOG 超前于 pyproject）
- 用例数 658 → **707**（+49：faultdb 20、RTM 6、场景注册表 7、reporting 11、
  examples 2、失败导出 2、补充），覆盖率 97.98% → **97.81%**
  （2417 语句 / 53 未覆盖，门禁 97% 达成），测试文件 33 → 37
- 术语统一：文档中"行覆盖率"→"语句覆盖率"（pytest-cov 实测口径）
- `run.py --replay` 改用完整回放链（`tcms.replay.ReplayChain`）替代简化判定
- CONTRIBUTING 引用本地化（移除仓库外 QA 文档链接）
- requirements.txt 单源化（`-e .[test,viz,lint]`）

## [1.5.1] - 2026-02

### 变更
- `tcms/network.py` 网关模型**现实化**：同步透传 → 异步缓冲转发（`send` 只投递到
  网关接收缓冲，时钟推进 + `step()` 泵出到期帧；转发时延 `latency`、缓冲容量
  `capacity`、溢出丢弃新帧、足迹 `trace` 防环、级联逐网关扩散——全部可观测、
  可审计），`BusNetwork` 接受 `clock` 参数（默认 `timebase.global_clock()`）
- `test_network.py` 24 → **30** 用例（新增：异步不可见直至 step、溢出丢弃计数、
  缓冲占用、级联双拍时延、足迹防环双向网关、负时延/零容量拒绝），`network.py`
  保持 100% 行覆盖

## [1.5.0] - 2026-02

### 新增
- `tcms/scenarios.py`：场景 YAML 外部化（`scenarios/*.yaml` 声明式故障场景，场景与代码分离）
- `tcms/network.py`：多网段拓扑（`BusNetwork` 命名网段 + `Gateway` 异步缓冲网关——接收 FIFO/转发时延/溢出丢弃/ID 过滤/足迹防环 + 级联扩散 + 转发统计/审计日志）
- `docs/tutorial.md`：从零到一完整教学教程（协议→总线→仿真→安全逻辑→网络→证据链）
- 依赖：`pyyaml>=6.0`（scenarios YAML 解析）

### 变更
- 用例数 612 → 652（+40：scenarios 17、network 24、测试文件 31 → 33），覆盖率 97.76% → **97.94%**（2186 语句 / 45 未覆盖，CI 门禁 `--cov-fail-under=97` 达成）
- `tcms/network.py` 为 100% 行覆盖（24 用例含防御分支：防环/发送失败容错/阻塞轮询）

## [1.4.0] - 2026-02

### 新增
- `tcms/bypass.py`：隔离/旁路开关状态机（旁路安全前提 + 审计日志 + 隔离组降级兜底）
- `tcms/canlog.py`：CAN 日志解析与回放（Vector .asc 格式，真实数据驱动验证）
- `run.py --replay`：真实 CAN 日志回放入口
- `tcms/voting.py`：2oo3→2oo2 容错降级路径（单通道故障自动降级 + 降级事件计数）
- `tcms/recorder.py`：事故冻结窗口（EB 触发前后快照，黑匣子语义）
- `tcms/nmt.py`：NMT 主站命令（CiA 301：Start/Stop/Pre-op/Reset）
- `tests/test_fault_chain.py`：端到端故障链（高负载→WCRT 超限→丢帧→看门狗→EB）
- `tcms/replay.py`：完整回放链（.asc → 虚拟时钟 → 联锁/ATP/看门狗/EBM → 告警断言）
- `tcms/timebase.py`：虚拟时间基（`VirtualClock`，确定性推进，全局统一时间源）
- `tcms/faultlife.py`：故障生命周期台账（注入→传播→告警→恢复→归档）+ 场景 DSL
- `docs/safety_case.md`：EN 50128 思路安全论证映射表（SR → 实现 → 测试证据）
- 可视化扩展：`state_door.png`（门控状态机）、`state_overspeed.png`（超速防护状态机）
- 开源成熟度：`SECURITY.md`、`CODEOWNERS`
- 工程化：`pyproject.toml` 标准化、开源三件套（ISSUE/PR 模板、CONTRIBUTING、CHANGELOG）

### 变更
- 用例数 562 → 612，覆盖率保持 97.66%（CI 门禁 `--cov-fail-under=97` 达成）

## [1.3.0] - 2026-01

### 新增
- `tcms/busfault.py`：总线级故障注入（短路/断路→集体 Bus-Off→恢复）
- `tcms/jitter.py`：周期抖动/漂移统计（ppm）
- `tcms/seqcheck.py`：报文序列/时序违规检测（丢帧/重复帧/乱序帧/迟到）
- `tcms/faultlevel.py`：故障分级模型（四级映射处置）+ 故障注入编排器
- `tcms/atp.py`：超速监督分层 + 动态 EBI 曲线（Warning/SBI/EBI）
- `tcms/nmt.py`：CANopen NMT 心跳层（CiA 301）
- `tcms/voting.py`：2oo3 速度表决
- `tcms/interlocks.py`：牵引-制动互锁 / 方向-速度联动 / 车门-站台联动

### 变更
- 用例数 366 → 507，覆盖率 97.49% → 98.10%
- CI 增加 lint（ruff）与 demo-smoke job

## [1.2.0] - 2025-12

### 新增
- `tcms/ebr.py`：EBR 硬线回路（得电缓解/失电制动 fail-safe + 断点诊断）
- `tcms/exec_feedback.py`：EB 执行反馈闭环（压力+回执+牵引切除三重证据）
- `tcms/busload.py`：总线负载率统计与压测（位级帧模型）
- `tcms/schedulability.py`：WCRT 可调度性分析（Tindell 迭代）+ ID 分配审计
- `tcms/bus.py`：硬件接口抽象层（TCMS_BUS_INTERFACE 环境变量）
- EBM 司机缓解操作序列（手柄回零 + 缓解按钮保持）

## [1.1.0] - 2025-11

### 新增
- `tcms/errstate.py`：CAN 错误状态机（ISO 11898-1：TEC/REC、Error-Active/Passive/Bus-Off）
- `tcms/recorder.py`：事件时序记录器（环形缓冲 + 过滤查询 + JSON/CSV 导出）
- `scripts/plot_timeline.py`：时序甘特图可视化
- hypothesis 属性测试套件

## [1.0.0] - 2025-10

### 新增
- `tcms/ebm.py`：紧急制动管理（模式×原因矩阵 + 缓解/复位闭环 + SIL2/SIL4 双通道表决）
- `tcms/interlocks.py`：安全联锁
- `tcms/watchdogs.py`：节点心跳看门狗
- 多节点 CAN 仿真器 + DBC 报文编解码 + CRC 校验
- demo.py 演示入口

### 首个里程碑
- 179 用例 / 97% 覆盖率，CI 全绿
