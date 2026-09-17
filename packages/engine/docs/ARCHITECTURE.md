# TCMS-CAN-Test 架构手册（Architecture）

> 本文件是平台的"演进操作手册"：讲清依赖方向、数据流、扩展点与
> 契约约定，让新贡献者/后续版本能在不破坏既有 960 用例的前提下
> 安全演进。细节功能见 [features.md](features.md)，安全论证见
> [safety_case.md](safety_case.md)。

## 1. 系统分层与依赖方向

```
┌────────────────────────────────────────────────────────────┐
│ 入口层    run.py（仓库态薄壳）· tcms-test（安装态入口）     │
│          tcms/cli.py（单一真源）· tcms/diagnose.py（自检）  │
├────────────────────────────────────────────────────────────┤
│ 应用层    场景编排 scenarios/*.yaml → tcms/scenarios.py    │
│          FMEA 故障字典 faults.yaml → tcms/faultdb.py        │
│          demo.py / examples/replay_demo.py                 │
├────────────────────────────────────────────────────────────┤
│ 业务层    安全逻辑：ebm ebr exec_feedback interlocks atp    │
│          voting bypass nmt watchdogs errstate              │
│          诊断分析：busload schedulability jitter seqcheck  │
│          recorder / replay / canlog / faultlife / lifecycle│
├────────────────────────────────────────────────────────────┤
│ 协议层    tcms.dbc（打包数据）→ protocol.py（编解码常量）   │
├────────────────────────────────────────────────────────────┤
│ 传输层    tcms/bus.py（总线工厂：virtual/pcan/vector/…）    │
│          python-can（BusABC）                              │
└────────────────────────────────────────────────────────────┘
```

**依赖规则**：上层可依赖下层；同层模块尽量不互相 import（确需时经
`tcms/protocol.py` 常量或 `tcms/bus.py` 工厂，不直接 new 总线）。
`tcms/bus.py` 是唯一允许直接触碰 `python-can` 的模块——业务层一律
注入 `bus` 对象，不感知接口类型（HIL 与 virtual 同套代码）。

## 2. 数据流（测试证据链）

```
DBC 协议 + 仿真器(线程) ──→ 虚拟/真实 CAN 总线 ──→ 测试用例断言
        │                          │                      │
        ▼                          ▼                      ▼
   报文编码/解码               recorder 统一时间线       JUnit/HTML/Allure
   (protocol/parser)          (事件+帧+故障台账)         (reporting)
                                 │
                                 ▼
   故障字典 FMEA(faultdb) → 场景 YAML(scenarios) → 台账(faultlife)
   RTM 追溯矩阵 ←── 用例 ←── 需求 SR-01~52
   CI 覆盖率门禁 ←── coverage.json → 徽章自证/Pages 趋势(latest.json)
```

"机器自证"是核心资产：README 徽章、文档站数字全部由 CI 产物
（JUnit / coverage.json）驱动，禁止手抄——新增任何"展示数字"必须
走 `scripts/gen_badges.py` / `scripts/render_pages.py` 链。

## 3. 扩展点食谱（How to extend）

### 3.1 新增一个报文（协议扩展）
1. 编辑打包数据 `tcms/tcms.dbc`，加 message + signals（含周期属性）。
2. `tcms/protocol.py`：加 ID 常量、`MESSAGE_NAMES`、枚举/阈值（如需）。
3. `tcms/simulator.py`：如需周期发送，在 `start()` 的 `_threads` 加
   `_spawn(tag, id, period)`，并实现 `_tick` 分支。
4. 补测试：周期/边界/枚举/丢报检测（仿 `tests/test_protocol.py`）。
   ⚠ 勿为演示而加第 9 种报文——先证明它服务某个 SR/故障键。

### 3.2 新增一个故障键（FMEA 字典）
1. `tcms/faults.yaml` 加一条 `F-TCMS-0xx`（fid/key 唯一，level/action
   须 ∈ faultlevel 合法集，与 faultlevel.FAULTS 重名条目校验一致）。
2. `tests/test_faultdb.py` 断言字典总数/唯一性自动覆盖（读文件不硬编码数）。
3. 若要被场景消费：确认 `faultlevel`/注入器支持该 key（v1.7 起处置
   回退统一字典，66 键全可注入）。

### 3.3 新增一个场景
1. `scenarios/<name>.yaml` 声明式编排（注入/恢复/断言），参照既有 13 例。
2. 头部注释写 F-TCMS 编号，与字典逐条对齐。
3. `tests/test_scenario_registry.py` 自动参数化执行目录内全部 YAML——
   无需改测试文件。保持注册表规模守卫 ≥5。

### 3.4 接入真实 CAN 硬件（HIL，Roadmap）
1. 插卡（PCAN/Vector/socketcan/slcan…）。
2. 设环境变量 `TCMS_BUS_INTERFACE/TCMS_BUS_CHANNEL/TCMS_BUS_BITRATE`。
3. `python run.py --doctor` → hardware 项应 PASS（自检即 HIL 探测）。
4. `pytest -m hardware`（marker 隔离的硬件用例）。
业务代码与测试**零改动**——切换只发生在 `tcms/bus.py` 工厂。

### 3.5 使用方接入（平台化：外部输入）
- 自带 DBC：`tcms.protocol.load_database(path="my.dbc")`。
- 自带日志：`tcms.replay.ReplayChain.from_asc("trip.asc")`（examples/ 有范式）。
- 脚本化：`import tcms` 顶层公共 API（load_database /
  load_fault_dictionary / make_bus / scenarios.run_yaml）。

## 4. 契约与约定（改前必读）

| 契约 | 位置 | 说明 |
|---|---|---|
| 版本单源 | `tcms/_version.py` | pyproject dynamic 读取；发布只改此处 + CHANGELOG |
| 打包数据 | `pyproject package-data` | DBC/faults.yaml 随 wheel；CI wheel 冒烟实证 |
| CLI 单源 | `tcms/cli.py` | run.py 与 tcms-test 均委托它（勿两处写逻辑） |
| 覆盖率门禁 | `pyproject [tool.coverage.report] fail_under=97` | CI 不再另传阈值（v1.9 收敛） |
| 测试分层 | markers：smoke/safety/hardware/slow/severity | 新用例按语义打标 |
| RTM | `tests/rtm.csv` + 元测试 | 新 SR 需同步矩阵 |
| 失败现场 | conftest `crash_site` fixture | 关键用例注册证据对象 |

## 5. 测试与发布流程

```bash
python run.py --level smoke        # 快速反馈（~1s，PR 必过）
python run.py --coverage           # 全量 + 覆盖率门禁（本地 ≈ CI）
python scripts/check_dist.py       # 分发契约自检（wheel 安装后实证）
python run.py --doctor             # 环境自检（含 HIL 状态）

# 发布（见 CONTRIBUTING.md）
# 1) tcms/_version.py 改版本 → 2) CHANGELOG.md 记变更 →
# 3) CI 全绿 → 4) git tag vX.Y.Z → release.yml 自动发 GitHub Release
```

> 铁律：任何"读起来像数字"的展示（用例数/覆盖率/帧数）都必须来自
> 机器产物或代码常量，禁止手工复制粘贴。
