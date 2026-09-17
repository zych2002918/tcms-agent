# TCMS CAN Test 测试计划（Test Plan）

> 对标真实轨道交通软件测试流程（EN 50128 V&V 计划思路）：测试不是"跑完用例"，
> 而是有**范围、分层、入口/出口准则、追溯、缺陷闭环**的系统工程。
> 本文件是项目的测试计划基线，与 `tests/rtm.csv`（机读追溯矩阵）、
> `docs/safety_case.md`（安全论证映射）配套使用。

   

## 1. 测试范围与对象

| 项 | 内容 |
|   |   |
| 被测对象（DUT） | TCMS 列车控制与管理系统安全逻辑：紧急制动（EBM/EBR）、联锁、ATP 超速监督、看门狗、错误状态机、事件记录、故障生命周期、网络拓扑、可调度性 |
| 被测接口 | CAN 总线（虚拟 python can + 可选真实硬件）；DBC 协议（tcms.dbc） |
| 测试环境 | 纯 Python 3.10+ 仿真（CI ubuntu × 3.10/3.11/3.12/3.13）；本地 venv |
| 不在范围 | 真实车辆硬件在环（HIL，`@pytest.mark.hardware` 预留）；第三方认证 |

## 2. 测试分层策略

| 层 | Marker | 范围 | 运行时机 | 目标时长 |
|   |   |   |   |   |
| 冒烟 Smoke | `smoke` | 核心安全路径（EBM 触发/缓解、联锁、CRC、协议编解码） | PR / 提交 | < 2 min |
| 回归 Regression | （默认全量） | 全部 960 用例 | main 合并 / 发版前 | ~1 min |
| 深度 Deep | `slow`/`property`/`fuzz` | 属性不变量、模糊、长时多节点 | 夜间/发版 | 数分钟 |

> 用 ` m "smoke"` 只跑冒烟层；` m "not slow"` 跳过深度用例。
> 每个用例可通过 docstring 或 marker 归属层级。

## 3. 入口准则（Entry Criteria）

  代码通过 `ruff check` + `ruff format   check`
  新增/修改功能必须**先写测试**（TDD），且通过 `pytest  q`
  需求变更先更新 `docs/safety_case.md` SR 表，再同步 `tests/rtm.csv`

## 4. 出口准则（Exit Criteria）

  全量 `pytest tests/   cov=tcms` **960 用例全绿**（硬件用例显式跳过）
  **语句覆盖率 ≥ 97%**（pyproject 门禁 `fail_under=97`）
  RTM 校验通过（`tests/test_rtm.py`：所有 SR 被追溯、引用文件存在）
  故障字典校验通过（`tests/test_faultdb.py`：与 faultlevel 双源一致）
  场景注册表全过（`tests/test_scenario_registry.py`：scenarios/*.yaml 可执行）
  `ruff format   check` 干净

## 5. 需求追溯（RTM）

  机读矩阵：`tests/rtm.csv`（req_id → 模块 → 测试文件 → 验证行为 → 状态）
  人类可读：`docs/safety_case.md` 第 1~3 节（SR → 实现 → 测试证据）
  校验：`tests/test_rtm.py` 保证矩阵不漂移（文件存在、SR 全覆盖、无重复行）
  约定：测试函数 docstring 首行可写 `TC TCMS <模块> <序号>` 用例 ID + 关联 SR
  （如 `TC TCMS EBM 001 · SR 01`），便于报告/缺陷单锚点引用

## 6. 故障管理与缺陷闭环

  **故障字典**：`tcms/faults.yaml`（经 `tcms/faultdb.py` 加载校验），203 条
  F TCMS xxx 记录（FMEA 字段：子系统/注入层/等级/动作/SIL/检测/注入/恢复）
  **注入约定**：场景/用例按故障键引用字典，禁止凭空造故障名
  **缺陷闭环**：缺陷单 → 修复 → **回归测试命名 `test_regress_<issue号>_...`**
  → 复验 → 全量回归。PR 模板关联需求/缺陷 ID
  **失败现场**：用例失败自动导出 recorder 时间线/总线快照到
  `reports/failures/<测试名>/`（conftest hook，见 README）

## 7. 产物与报告

| 产物 | 生成 | 消费者 |
|   |   |   |
| JUnit XML | `pytest   junitxml=reports/junit.xml` | Jenkins/GitLab/CI 汇总 |
| HTML 报告 | `pytest   html=reports/report.html` | 人读 |
| Allure | `  allure report` | 可视化趋势 |
| 覆盖率 | `  cov` / `coverage json` | 门禁 + 徽章自证 |
| 历史趋势 | `scripts/report_history.py` | 跨版本对比 |
| README 徽章 | `scripts/gen_badges.py`（JUnit + coverage.json） | GitHub 首页（CI 自动刷新） |
| Pages 实时报告 | `.github/workflows/pages.yml` → `docs/reports/` | 文档站最新结果 |

## 8. 版本与变更管理

  版本单一事实源：`pyproject.toml` version（发版同步 CHANGELOG/tag/文档）
  变更记录：CHANGELOG.md（Keep a Changelog）；重大设计决策补 docs/adr/
