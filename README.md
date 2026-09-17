# TCMS × AI —— 列车控制软件测试平台与 AI 测试工程师 Agent

> 面向列车网络控制系统（TCMS / CAN）的**可执行、可自证**测试工程平台：
> 真实资产 → 领域引擎 → 知识底座 → Agent 查证 → 真实执行 → 机器自证。
> 全部离线可跑，无需任何 API key。

[![Python](https://img.shields.io/badge/Python-3.11+-2dd4a0)](#快速开始)
[![License](https://img.shields.io/badge/license-MIT-8ca0c0)](#license)

---

## 这是什么

本仓由三个原独立仓库整合而成（**各自的完整 git 历史均已保留**）：

| 原仓库 | 现位置 | 角色 |
|---|---|---|
| `tcms-can-test` | `packages/engine` | **领域引擎**：CAN 仿真、DBC 编解码、场景执行器、安全逻辑与真实资产 |
| `tcms-ai-platform` | `packages/platform` | **平台**：资产模型 + 知识底座（图谱/检索）+ Web UI |
| `tcms-ai-testgen` | `packages/testgen` | **生成器**：受约束 DSL 生成 → 编译真实 pytest → 变异杀毒 / 反思自愈 |
| —— | `packages/agent` | **★ 全流程测试工程师 Agent**（开发中，见路线图） |

**合并带来的实质收益**：原三仓的 CI 需要 `actions/checkout` **别的仓库**来当兄弟目录
（平台与生成器的 CI 都依赖 `zych2002918/tcms-can-test`），路径假设脆弱、失效时还会
**静默降级为 skip**。合并后上游就是本仓的 `packages/engine`，耦合全部变为仓内相对路径。

---

## 架构（自下而上）

```
┌─ packages/agent ──── Agent 认知层：loop / 工具面 / 四层记忆 / 评测   ← 主战场
├─ packages/platform ─ 知识底座：知识图谱 + 混合检索 + 资产模型 + FastAPI + Web UI
├─ packages/testgen ── 生成与自证：受约束 DSL → 真实 pytest → 变异杀毒 / 反思自愈
└─ packages/engine ─── 领域引擎：DBC / 故障字典 / 场景执行 / 安全逻辑
```

依赖方向严格单向：`agent → platform → engine`，`testgen → engine`；同层不互相 import。

---

## 快速开始

前置：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/zych2002918/tcms-agent.git
cd tcms-agent

# Windows
start.bat
# macOS / Linux
bash start.sh
```

启动器会自动 `uv sync`（把各成员装进同一个 venv）并把上游引擎指向 `packages/engine`，
随后打开 <http://127.0.0.1:8000>。**全部功能离线可用**，无需任何 key。

### 手动命令

```bash
uv sync                                    # 同步工作区（全部成员 + dev 依赖）
uv run pytest                              # 从仓根跑全部成员的测试
uv run ruff check packages                 # 全仓 lint
uv run python -m uvicorn tcms_ai_platform.server.app:app --port 8000   # 起 Web UI
```

---

## 目录结构

```
tcms-agent/
├── packages/
│   ├── engine/     领域引擎（原 tcms-can-test）              → import tcms
│   ├── platform/   平台 + 知识底座 + Web（原 tcms-ai-platform）
│   │   ├── src/tcms_ai_platform/
│   │   └── web/    前端（React 18 + TS + Vite；构建产物有意入库）
│   ├── testgen/    受约束生成与自愈评测（原 tcms-ai-testgen）
│   └── agent/      ★ LangGraph Agent
├── docs/           跨包文档（架构 / 决策记录 / 整合说明）
├── pyproject.toml  uv 工作区根配置（虚拟工作区）
├── conftest.py     为全部成员统一解析上游引擎位置
└── start.sh / start.bat
```

---

## 测试与门禁

| 成员 | 命令（成员目录内） | 门禁 |
|---|---|---|
| engine | `pytest tests -q` | 覆盖率 `fail_under=97` |
| platform | `pytest -q` | ruff + pytest + vitest |
| testgen | `pytest tests --cov=tcms_ai_testgen` | 覆盖率 `fail_under=90` |
| 全仓 | `uv run pytest`（仓库根） | 一把梭，便于开发态快速反馈 |

CI 见 `.github/workflows/ci.yml`：按成员分 job（保留各自 rootdir 配置与覆盖率门禁），
**不再 checkout 外部仓库**。发布走 `.github/workflows/release-engine.yml`，
tag 约定为 `成员名-v*`（如 `engine-v1.13.0`）。

---

## 路线图

- [x] **R0 三仓合一** —— monorepo 工作区、统一 CI、路径去耦合、历史保留
- [ ] **R1 Agent 骨架** —— LangGraph 核心循环 + 只读工具面，跑通一个真实测试目标
- [ ] **R2 Harness 化** —— 工具注册表、预算控制、轨迹落盘与回放、中断审批
- [ ] **R3 写权限工具面** —— 沙箱生成 → 编译 → 真实执行（分级权限 + 人在环路）
- [ ] **R4 RAG 升级** —— 真 embedding、图谱入融合、rerank、引用强制校验
- [ ] **R5 四层记忆** —— 工作 / 情景 / 语义 / 程序性 + 离线巩固与写入门禁
- [ ] **R6 评测体系** —— 任务集、指标、A/B 回归门禁、轨迹回放
- [ ] **R7 对比实验** —— 自研最小 loop 与框架版同集对比

---

## License

MIT —— 见各成员目录下的 `LICENSE`（`packages/engine`、`packages/platform`、`packages/testgen` 均为 MIT）。
