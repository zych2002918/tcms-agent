# tcms-agent —— TCMS 全流程测试工程师 Agent

> 用 LangGraph 编排的领域 Agent：在**真实**列车控制测试资产上查证、并让每一步
> 都可审计、可回放、可机器校验。

本包是 `tcms-agent` monorepo 的认知层，位于 `packages/platform`（知识底座）
与 `packages/engine`（领域引擎）之上。

---

## 快速开始

```bash
# 在仓库根（会自动装好 engine/platform/agent 三个成员）
uv sync

# 自检：知识底座规模 / LLM key / 轨迹存储
uv run tcms-agent doctor

# 看工具面与权限级别
uv run tcms-agent tools

# 跑一个目标（默认离线规则臂，无需任何 key）
uv run tcms-agent run "验证车门故障必须触发降级处置"

# 用真 LLM 决策（需 key，否则如实降级并提示）
uv run tcms-agent run "验证车门故障必须触发降级处置" --llm

# 回放某次运行的轨迹（逐超级步）
uv run tcms-agent history run-xxxxxxxxxxxx
```

---

## 这张图长什么样

```
START → plan → agent ─┬─(有工具调用 且 预算未超)→ act ─┬─(有待审批项)→ approve → agent
                      │                              └─(无)──────────────────→ agent
                      └─(收尾 / 预算耗尽)────────────→ verify → report → END
```

- `plan`：目标 → 计划清单 + 锚定目标故障（规则解析，落在真实 `faults.yaml` 上）
- `agent`：**唯一的自由决策节点** —— LLM 决定调哪个工具，或收尾
- `act`：执行**不需要审批**的工具（权限门禁 + 审计 + 观察回填）
- `approve`：**human-in-the-loop 审批**（全图唯一 `interrupt()` 点）
- `verify`：**引用强制校验** —— 结论引用的每个资产 id 回真实图谱核对
- `report`：汇总结构化 verdict，并**披露被拒的写入**

### 人在环路怎么用

```bash
# 默认：只能读到 R2（真执行），碰不到持久化
uv run tcms-agent run "验证车门故障必须触发降级处置"

# 开放 R3：终端会暂停并逐项询问是否批准写入
uv run tcms-agent run "..." --allow-write

# 非交互场景
uv run tcms-agent run "..." --allow-write --no    # 一律拒绝
uv run tcms-agent run "..." --allow-write --yes   # 一律批准（不推荐，仅受控演示）
```

**默认拒绝**：没有审批者（`approver=None`）时一律拒绝——没人在场就不写。
拒绝也会作为工具结果回填给 Agent，并写进报告与轨迹。

## 两个臂

| 臂 | 触发 | 用途 |
|---|---|---|
| `offline-rule` | 无 key / `--offline` | 确定性规则参考实现；走**同一张图同一套工具**，可复现、可进 CI |
| `llm` | 有 key 且 `--llm` | 真模型决策 |

结果里的 `model_kind` 如实标注，**不会把规则结果说成 AI 决策**。

## 工具权限（四级已全部落地）

| 级别 | 工具 | 现状 |
|---|---|---|
| **R0 只读**（9） | kb_search / kb_filter_assets / symptom_diagnose / kb_node / list_scenarios / fault_detail / list_requirements / **dsl_reference** / **list_drafts** | ✅ |
| **R1 沙箱写**（1） | **draft_test_case**（受约束 DSL，编译期校验，只写沙箱可丢弃） | ✅ |
| **R2 真实执行**（3） | run_scenario / verify_fault_action / **run_draft**（子进程 + 真超时 + 产物归档） | ✅ |
| **R3 持久化**（2） | write_memory（引用门禁）/ promote_artifact | ✅ 需人工审批 |

**完整闭环**：查清语义(R0) → 造用例(R1) → 编译成真实 pytest 并跑(R2) → 沉淀(R3，人工审批)。

三道**互相不可替代**的闸门：权限级别（存不存在）→ 人工审批（人放不放行）
→ 机器门禁（内容能不能核实）。高权限工具既不出现在送给模型的 schema 里，
也不可在 `invoke()` 时执行。

**反思自愈由 Agent 自己做**：`run_draft` 只返回真实失败要点（`failure_lines`），
不内置固定修复循环——Agent 自己决定要不要改写、怎么改写（同 `draft_id` 覆盖即迭代）。

## 评测（Eval Harness）

回答"这个 Agent 到底能不能干活、每个设计选择有没有用"。

```bash
uv run tcms-agent eval tasks                 # 列出任务集
uv run tcms-agent eval run                   # 跑全部可用对照臂
uv run tcms-agent eval run --arms rule,llm --out a.json
uv run tcms-agent eval gate --candidate a.json --baseline b.json   # 回归门禁
```

任务集 11 条，四类：`verify`（能否验证故障→处置）/ `author`（能否自己写用例并真跑通过）/
`diagnose`（症状多跳）/ `honest_fail`（无关输入能否如实拒绝）。
**负例与正例同权**——"什么都说好"的 Agent 会被扣分。

**实测基线（规则臂）**：10/11 达成、幻觉率 0%。唯一失败项是如实保留的已知缺口
（`T-VERIFY-CRC-LOOSE`：口语省略措辞），已固化为 CI 门禁
（`test_baseline_task_set_gate`）。

**LLM 臂 vs 规则臂**：达成率相同，但 LLM **更省步数**（3.45 vs 4.82）——
它更常直接调 `verify_fault_action` 拿引擎证据，跳过中间检索。

> 诚实说明：**记忆与重排在这套任务集上测不出差异**，原因是仪器不对而非效果不存在
> （重排的仪器是检索 golden：那里实测 top-1 由 12/14 → 14/14）。
> 详见 `docs/ARCHITECTURE.md` §2.8 与 ADR-016。

## 四层记忆

| 层 | 实现 | 写入纪律 |
|---|---|---|
| 工作记忆 | LangGraph state（当前 trajectory） | 自动 |
| **情景记忆** | `memory/journal.py` 运行日志 JSONL | 自动（机器事实） |
| 语义记忆 | platform 知识底座（资产/图谱/检索） | 来自真实资产 |
| **程序性记忆** | `memory/consolidate.py` 离线巩固出的技能 | **两道门禁 + 人工 --write** |

```bash
# 看记忆规模与已有技能
uv run tcms-agent memory stats

# 试召回：这个目标会想起什么
uv run tcms-agent memory recall "验证车门故障不能发车"

# 离线巩固：从历史运行提炼可复用经验
uv run tcms-agent memory consolidate --min-occurrences 2          # 只看提案
uv run tcms-agent memory consolidate --min-occurrences 2 --write  # 过门禁后写入

# 关闭记忆（做「有记忆 vs 无记忆」对照）
uv run tcms-agent run "..." --no-memory
```

跑两次同族目标即可看到闭环：首次「无相关历史记忆」→ 第二次「召回 1 条」→
巩固出技能 → 之后「召回 N 条（过往运行 x / 沉淀技能 y）」。

**写入为什么必须过门禁**：一条编造的"经验"一旦入库，会被后续召回反复强化。
所以要求 ① 引用的资产真实存在；② 必须指出它来自哪些运行，且这些 run_id 真的在日志里。

**召回口径**：字符重合度，不是语义（与知识底座同一通道）。真语义通道属 R6 范围。

## 测试

```bash
cd packages/agent
pytest -q      # 75 passed：无 API key，全部离线可复现
```

覆盖：权限门禁（含"被拒的调用绝不能真的执行"）、诚实错误、审计记录、
引用校验（含"校验器不得误杀真实故障键"的自证）、端到端确定性、预算强制、
轨迹持久化与回放、审批三态（默认拒绝/批准落盘/拒绝回填）、
引用门禁独立于审批生效、报告披露被拒写入、
**DSL 词表与编译器白名单防漂移**、**沙箱隔离（草稿不得进仓库）**、
**自我修复闭环（失败→改→重跑通过）**。

## 文档

- 架构：[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)
- 决策记录（含"为什么用 LangGraph、什么必须自研"）：[`docs/decisions.md`](../../docs/decisions.md)
