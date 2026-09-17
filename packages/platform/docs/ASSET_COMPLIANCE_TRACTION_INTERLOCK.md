# 资产合规审计：牵引丢失处置"取决于原因" + EB 环线联锁联合状态

> 审计日期：2026-09-09 · 覆盖仓库：`tcms-can-test`（上游引擎资产）/ `tcms-ai-platform`（平台模型·图谱·组合）
> 触发：用户提问"牵引丢失的处置目前是否是线性的？现实取决于具体原因；门/完整性等严重原因应联合 EB 环线失电，同时失去牵引并紧急制动"。

## 1. 审计结论（先回答"是否线性"）

**是——原资产处置是"线性单值"的**，但这不是实现缺陷，而是表达缺口：

| 层 | 原状 | 证据 |
|---|---|---|
| 上游故障字典 | `traction_loss`(F-TCMS-006) 单值 `action=derate`，202 键全部无"原因条件化"字段 | `tcms/faults.yaml` 全库 0 条 conditional-action 字段 |
| 平台模型 | `FaultDef` 仅单值 `action`，无处置说明透出 | `core/models.py` |
| 引擎联锁 | EB 环线得电/失电语义真实存在（`ebr.py`）、EB 施加须压力+回执+**牵引切除**三重证据（`exec_feedback.py`）、门-车联锁（`interlocks.py`） | 引擎模块自证 |
| 覆盖场景 | `traction_loss` 只出现在 2 个 derate 场景；无"严重原因→EB"联合注入 | `heartbeat_traction_loss.yaml` 等 |
| 平台知识图谱 | fault 节点 props 只有单值 action；无"完整性/环线失电→牵引切除"机制概念与联锁边 | `knowledge/graph.py` |

现实语义（用户陈述 = 正确）：牵引丢失后是否紧急停车**取决于原因**——
- **会 EB**：列车解体（完整性丧失）、运行中车门打开等 SIL4 严重安全故障 → EB 环线失电 → **同时失去牵引并施加紧急制动**；
- **不会 EB**：可恢复部件故障、站台停稳后的正常操作指令 → 仅降级/恢复。

## 2. 已落地的合规修复（跨两仓，门禁全绿）

### 2.1 上游资产 `tcms-can-test`
- `tcms/faults.yaml`
  - 新增 **F-TCMS-203 `integrity_loss`（列车完整性丧失）**：critical / emergency_brake / SIL4 / 网络域，desc 与 detect/inject/recovery 描述"完整性回路失电 → EB 环线失电 → 同时失去牵引并紧急制动"；
  - `traction_loss` 增加 **`action_note`**（原因条件化说明）：可恢复/正常指令 → derate；完整性/门联锁等 SIL4 原因 → 走 EB 环线（由对应联锁资产承担）。
- 新增场景 `scenarios/integrity_loss_eb_loop.yaml`（**严重安全联锁联合紧急制动**）：同时注入 `integrity_loss` + `door_open_moving`，双双断言 `emergency_brake` —— 演示"完整性 + 门联锁同现 → EB 环线失电联合紧急制动"的联合状态（不再是线性单故障处置）。
- 全量测试 **959 passed + 1 skipped**（原 958 → 新场景注册表用例）。

### 2.2 平台模型与 API `tcms-ai-platform`
- `FaultDef` + loader + `/api/faults`(+detail) 透出 **`action_note`**（处置取决于原因，可空）。
- **compose_seq 联锁联合提示**：当组合含 门域/牵引域 故障且整链收尾期望 `emergency_brake` 时，响应附加：
  - `interlock_note`（处置取决于原因的诚实说明：EB 环线 vs derate 两条分支）；
  - `interlock_scenarios`（引用了覆盖 SIL4 严重原因的**现成真实联锁场景** `integrity_loss_eb_loop.yaml` / `door_open_moving_eb.yaml`，可点即执行，不臆造步骤）。
  无 EB 收尾的组合不提示（不误标）。
- Web：AssetsPage 故障详情与 Agent 组合面板展示 `action_note` / interlock 提示。

### 2.3 平台知识图谱与概念卡
- `domain/data/domain_safety.json`：新增概念卡「EB 环线失电与牵引切除联锁」（8→9 概念语义卡片）——讲解"环线得电=缓解/失电=制动；严重原因→同时失去牵引并 EB"。
- `domain/data/domain_ebm.json`：新增联锁知识 **ILK-INTEGRITY-EB**（列车完整性/门联锁 → EB 环线失电联锁）。
- `domain/enrichment.py`：concept 注入对含 "EB" 的卡关联 `function:F-EBM`；interlock 关联 fault 增加 `integrity_loss`/`door_open_moving` 的 `enforces` 边（图谱可达，非孤立）。
- 计数同步（纪律）：203 故障 / 104 场景 / 基础图 519 节点 / 508 资产文档 / enrich 655 节点，测试与 README/docs 全链更新。

## 2.4 举一反三：interlock→fault 关联机制化（不再关键词猜）

审计追问"只加这一个关联？图谱应按现实举一反三"——验证发现关键词猜关联存在**误连/漏连**
（`door_open_moving`(字典 EB) interlock 邻居为空；`door_fault` 因"门"字被误连到
ILK-INTEGRITY-EB / ILK-EB-DECISION）。修复为机制级：

- **显式 fault_keys**：`domain_ebm.json` 每条 interlock 声明其真实约束的故障键
  （依据 = 引擎函数语义 + 场景 expect 断言），enrichment 注入只读声明，废除关键词猜测：
  - `ILK-DOOR-MOTION → door_open_moving, door_fault`（移动×门开/门故障违规）
  - `ILK-EB-DECISION → overspeed, door_open_moving`（超速或门联锁冲突 → EB 请求）
  - `ILK-OVERSPEED → overspeed`；`ILK-PANTOGRAPH-ARC → pantograph_arc`
  - `ILK-TRACTION-BRAKE → traction_brake_conflict`
  - `ILK-INTEGRITY-EB → integrity_loss, door_open_moving`（SIL4 严重原因 → EB 环线）
- **同型条件化补齐**（都是引擎真实机制，非杜撰）：上游 faults.yaml 给 `door_fault`
  加 action_note（静止=禁发车/derate；**运行中=门-车联锁违规走 EB**，见 door_open_moving）、
  `overspeed` 加 action_note（处置取决于 ATP 三级：Warning/SBI→derate；达 EBI→EB 请求）。
- **回归测试** `test_interlock_fault_links_are_explicit`：锁定显式声明结果（door_open_moving
  必被三联锁约束、door_fault 不再误连、fault_keys 均指向真实键），防回退到关键词猜。

诚实边界：`integrity_loss / door_open_moving` **不**加 `-causes-> traction_loss` 因果边——
EB 环线失电导致的牵引切除是**预期联锁动作**，不是"牵引丢失部件故障"的成因；硬连会污染
诊断链（把 EB 伴随当成故障因果）。该联合语义由 interlock + action_note + 概念卡承载。

## 3. 诚实边界（不夸大）
- 场景执行器（ScenarioRunner）按"注入故障 → 期望 = 故障字典默认处置"断言；**它不做环线失电的物理级联仿真**。真正的"EB 施加须牵引切除"证据在引擎模块 `exec_feedback.py`（三重证据），不是 YAML 场景能断言的。
- 新增 `integrity_loss` 是真实安全概念（列车完整性监视 / 分离检测），非杜撰；其 EB 处置与既有 `door_open_moving`(critical/EB) 同属 SIL4 严重原因分支。
- `action_note` 是"说明字段"：默认处置仍是 derate（可恢复分支合规），严重分支由真实联锁资产承担 —— 没有把 traction_loss 改成 EB 去冒充条件处置。

## 4. 门禁
- 上游 `tcms-can-test`：`pytest -q` = 959 passed + 1 skipped（含新场景全过 + 无孤儿：新键有场景引用）。
- 平台 `tcms-ai-platform`：`pytest -q` = 221 passed + 1 skipped；ruff clean；tsc 0；vitest 16/16。
