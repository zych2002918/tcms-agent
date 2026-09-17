# TCMS × AI —— 列车控制软件测试平台与 AI 测试工程师 Agent

> 面向列车网络控制系统（TCMS / CAN）的**可执行、可自证**测试工程平台 + 一个**真正会干活的
> 领域 Agent**：真实资产 → 领域引擎 → 知识底座 → Agent 查证/造用例/真跑 → 机器自证。
> **全部离线可跑，无需任何 API key。**

[![Python](https://img.shields.io/badge/Python-3.11+-2dd4a0)](#快速开始)
[![tests](https://img.shields.io/badge/tests-1522%20passed-2dd4a0)](#测试与门禁)
[![License](https://img.shields.io/badge/license-MIT-8ca0c0)](#license)

---

## 这是什么

本仓由三个原独立仓库整合而成（**各自完整 git 历史均已保留**），并在其上构建了 Agent 层：

| 原仓库 | 现位置 | 角色 |
|---|---|---|
| `tcms-can-test` | `packages/engine` | **领域引擎**：CAN 仿真、DBC 编解码、场景执行器、安全逻辑与真实资产 |
| `tcms-ai-platform` | `packages/platform` | **平台**：资产模型 + 知识底座（图谱/检索）+ Web UI |
| `tcms-ai-testgen` | `packages/testgen` | **生成器**：受约束 DSL → 编译真实 pytest → 变异杀毒 / 反思自愈 |
| —— | `packages/agent` | **★ 全流程测试工程师 Agent**（本项目主战场） |

```
┌─ packages/agent ──── Agent 认知层：loop / 四级工具面 / 四层记忆 / RAG / 评测
├─ packages/platform ─ 知识底座：知识图谱 + 混合检索 + 资产模型 + FastAPI + Web UI
├─ packages/testgen ── 生成与自证：受约束 DSL → 真实 pytest → 变异杀毒 / 反思自愈
└─ packages/engine ─── 领域引擎：DBC / 故障字典 / 场景执行 / 安全逻辑
```

依赖方向严格单向：`agent → platform → engine`，`testgen → engine`。

---

## Agent 能做什么

```
START → recall → plan → agent ─┬─(有工具调用 且 预算未超)→ act ─┬─(待审批)→ approve → agent
                               │                              └─(无)─────────────→ agent
                               └─(收尾 / 预算耗尽)────────────→ verify → report → END
```

**四级工具权限**（已全部落地，共 15 个工具）：

| 级别 | 工具 | 管控 |
|---|---|---|
| **R0 只读** | kb_search / kb_node / list_scenarios / fault_detail / list_requirements / dsl_reference / list_drafts / kb_filter_assets / symptom_diagnose | 自由调用 |
| **R1 沙箱写** | draft_test_case（受约束 DSL，编译期校验，只写沙箱可丢弃） | 只写沙箱 |
| **R2 真实执行** | run_scenario / verify_fault_action / run_draft | **子进程 + 真超时 + 产物归档** |
| **R3 持久化** | write_memory（引用门禁）/ promote_artifact | **人工审批** |

**三道互相不可替代的闸门**：权限级别（存不存在）→ 人工审批（人放不放行）
→ 机器门禁（内容能不能核实）。高权限工具既不出现在送给模型的 schema 里，
也不可在 `invoke()` 时执行。

**四层记忆**：工作（state）/ 情景（运行日志）/ 语义（知识底座）/ 程序性（离线巩固出的技能）。
巩固走**两道门禁**（引用必须真实 + 证据运行必须存在于日志），`--write` 前默认只出提案。

**RAG**：BM25 + 向量双路 RRF 召回，**图谱作为纯补充路**并入融合；
特征重排；**引用强制校验**覆盖工具链与结论正文。

---

## 一个可能最有意思的部分：框架版 vs 手写版

为回答"自己写的怎么跟成熟框架比"，仓里有一份**手写最小 loop**（`nolib/`，175 有效行），
与本项目的 LangGraph 版**共用同一批节点、同一套工具、同一个模型**，只替换编排层：

```bash
uv run tcms-agent nolib --verify      # 出对照报告 + 8 项能力断言
```

实测结论（可复跑）：

```
任务 11 条：graph 91% / nolib 91%
平均步数 4.82 / 4.82；平均工具调用 3.82 / 3.82
逐条一致（11/11）
```

**框架不参与决策，因此不改变任务结果**；它买到的是能力而非行数：

| 能力 | graph | nolib |
|---|---|---|
| 状态落盘 / 断点续跑 | ✅ | ❌ |
| **人工审批中断** | ✅ 暂停等人 | ❌ 自动拒绝（并如实披露） |
| 轨迹回放 / 流式观测 | ✅ | ❌ |

---

## 快速开始

前置：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/zych2002918/tcms-agent.git && cd tcms-agent
start.bat          # Windows       —— 自动 uv sync 并起 Web UI
bash start.sh      # macOS / Linux
```

### Agent 常用命令（全部离线可用）

```bash
uv run tcms-agent doctor                          # 环境自检
uv run tcms-agent tools                           # 看工具面与权限级别
uv run tcms-agent run "验证车门故障必须触发降级处置"    # 跑一个目标（离线规则臂）
uv run tcms-agent run "..." --llm                 # 用真模型（需 key，否则如实降级）
uv run tcms-agent run "..." --allow-write         # 开放 R3（终端逐项审批）
uv run tcms-agent history <thread_id>             # 轨迹回放（逐超级步）
uv run tcms-agent memory stats|recall|consolidate # 四层记忆
uv run tcms-agent eval tasks|run|gate             # 评测 / A-B / 回归门禁
uv run tcms-agent nolib --verify                  # 框架 vs 手写对照
```

---

## 测试与门禁

| 成员 | 命令（成员目录内） | 门禁 |
|---|---|---|
| engine | `pytest tests -q` | 覆盖率 `fail_under=97` |
| platform | `pytest -q` | ruff + pytest + vitest |
| testgen | `pytest tests --cov=tcms_ai_testgen` | 覆盖率 `fail_under=90` |
| **agent** | `pytest -q` | 含**评测回归门禁**：规则臂 ≥10/11 且零幻觉 |
| 全仓 | `uv run pytest`（仓库根） | 一把梭 |

CI 见 `.github/workflows/ci.yml`：按成员分 job，**不 checkout 外部仓库**。
根 `conftest.py` 有一道护栏：monorepo 下找不到上游引擎时**大声中止**，
而不是放任几百条测试集体静默跳过（这个坑踩过两次）。

---

## 文档

| 文档 | 内容 |
|---|---|
| `docs/ARCHITECTURE.md` | 架构说明（现状，非愿景）：图拓扑、四级权限、子进程执行、审批拆分、四层记忆、RAG、评测、nolib 对照 |
| `docs/decisions.md` | 18 条 ADR：每条含背景 → 决策 → 理由 → 代价，以及**踩过的坑** |
| `packages/agent/README.md` | Agent 使用说明 |

**这个项目的性格**：文档里写的每个数字都能在代码或测试里指到；
测不出差异的评测会被标注"仪器不对"而不是当成"没有差异"；
被证伪的设计选择会写进 ADR 的"代价"里。

---

## License

MIT —— 见各成员目录下的 `LICENSE`。
