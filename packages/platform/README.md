# TCMS × AI — 列车软件测试平台

> **让 AI 测试工程师"能干活、能自证、看得见"** —— 面向列车网络控制系统（TCMS / CAN）的本地测试平台：
> 真实资产 → 知识底座 → Agent 查证 → 真实执行 → 机器自证。全部可离线运行。

[![Python](https://img.shields.io/badge/Python-3.11+-2dd4a0)](#快速开始)
[![FastAPI](https://img.shields.io/badge/FastAPI-Web_UI-4ca6ff)](#)
[![React](https://img.shields.io/badge/React-18+-8b7cf6)](#)
[![pytest](https://img.shields.io/badge/pytest-221%20passed-2dd4a0)](#测试--门禁)
[![CI](https://github.com/zych2002918/tcms-ai-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/zych2002918/tcms-ai-platform/actions)
[![license](https://img.shields.io/badge/license-MIT-8ca0c0)](#license)

![平台总览](docs/dashboard-preview.png)

---

## 它解决什么问题

一条流水线回答三个"会被问穿"的问题：

```
真实资产(DBC/FMEA/场景/RTM)
      │  L1 资产模型化
      ▼
知识底座(图谱 655 节点 · 13 系统域 · 54 因果边 · 644 检索文档)
      │  混合检索 + 图谱证据 + 出处链
      ▼
Agent(Harness · 8 类真实任务 / 自由目标 / 症状多跳诊断)
      │  规则确定性 + 可选 LLM(无 key 自动降级)
      ▼
真实执行(TCMS 引擎) → KB 锚定评审(6 维) → 反思自愈 → run 沉淀
      │
      ▼
机器自证(检索 golden · 诊断 golden · 对抗集 · 真实断言 · CI)
```

> 一句话定位：它不是一个"接了大模型的聊天框"，而是**给 AI 测试工程师装护栏**——
> LLM 想说话可以，但每句话都要能落到真实资产、真实执行、可追溯证据上。

---

## 核心亮点

### 🧠 真实资产驱动的知识底座
- 203 条 FMEA 故障（13 系统域）/ 104 个可执行场景 / 22 报文·116 信号 / 52 安全需求，全部来自上游引擎 `tcms-can-test`；
- **GraphRAG 风格混合检索**：BM25 词法 + 向量 + 图谱证据三通道 RRF 融合；默认**字符级确定性通道**（离线、可复现），真语义近义为**可选增强**（`TCMS_EMBEDDER=api` 接入 OpenAI 兼容 embedding，无 key/失败自动降级，绝不冒充语义）；
- **出处链**：检索与诊断的每条证据都可点到资产 `file:key`（`faults.yaml`/`symptoms.yaml`/`tcms.dbc`…）；
- **弱证据升级**：A、B 无直接边时给出可溯源的资产锚点与 2 跳路径，不靠黑盒硬编。

### 🕸️ 知识图谱工作台（2D / 3D）
- 13 系统域基础骨架图即开即见；滚轮缩放、拖拽平移、双击适配；
- **单击节点=选中看详情**（图上高亮环），**双击=以它为中心跳转**，⬅ 返回栈可回退浏览；
- 节点可直达动作：详情侧栏一键"以它为中心扩展 / ▶ 去 FaultLab 演示"；
- 图谱内可看到该资产近期真实执行记录（`recent_runs`）与关联证据。

![知识图谱工作台](docs/graph-2d-preview.png)

### 🩺 症状多跳诊断（无码故障 → 建议链）
- 输入"仪表盘闪烁但无故障码"这类现象 → 12 个症状资产 + 54 条带依据因果边 ≤3 跳取候选；
- 每候选给"验证动作 / 查哪个信号 / 用哪个场景复现"与**排序分**（依据充分性，非概率）；
- **多轮追问**：`session_id` 锚点记忆（只存证据引用，不存摘要），"刚才那个部位"可接着聊；
- **可分性澄清**：候选分不开时返回"缺哪个观测"，不硬排第一；证据不足诚实说不知道，**绝不编造故障码**。

### 🤖 Agent 工作台（查证 = 检索→执行→评审→反思）
- 8 类真实故障任务 + 自由目标（"车门故障了还能发车吗"）；
- **真实执行 + 6 维 KB 锚定评审 + 反思自愈**：首轮不达/评审有缺口 → 自动换场景重跑 → 复评（非 LLM 自我感觉，评审全部可溯源）；
- 结果带事件流感（plan→…→reflect→report）、四维雷达与轨迹审计。

### 🧰 让"外部 Agent"也能指挥 TCMS
- **受约束 function-calling**（配 key）：LLM 可在真实只读工具面内自主查证 —— `kb_search` / `kb_filter_assets` / `symptom_diagnose` / `kb_node` / `list_scenarios`；参数经校验、未开放工具拦截、回复自证 used_tools（`POST /api/agent/toolassist`）；
- **最小 MCP server**（零第三方依赖，stdio）：任意 MCP 客户端可直接指挥平台查证：
  ```bash
  python -m tcms_ai_platform.agent.mcp_server
  ```
  （暴露 5+1 工具：检索/资产枚举/症状诊断/节点/场景；`run_scenario` 需引擎接线，否则返回诚实错误）

### 🎬 FaultLab 故障动画 & 🎛️ 场景编排
- 真实场景逐帧播放：故障注入→检测→处置→恢复，每事件可溯源到 场景/故障字典/引擎断言/示意模型 四级来源；
- 手动编排 + AI 顾问多轮对话，建议可一键采纳并真实执行。

![FaultLab 故障演示](docs/faultlab-preview.png)

---

## ⚡ 快速开始（2 条命令，无需任何 key）

```bash
git clone https://github.com/zych2002918/tcms-ai-platform.git
cd tcms-ai-platform

# Windows：
start.bat
# macOS / Linux：
bash start.sh
```

自动建 venv → 装依赖 → 起服务 → 打开 `http://127.0.0.1:8000`。**全部功能离线可用**（Agent 默认离线 Mock，检索/执行/评分零成本）。

### 可选增强（不配也能完整演示）
| 目的 | 配置 |
|---|---|
| LLM 决策 / function-calling | 环境变量 `DASH_API_KEY`（或 DeepSeek/OpenAI）或设置页填写 base_url/model/key |
| 真语义向量通道 | `TCMS_EMBEDDER=api`（可加 `EMBEDDING_MODEL`），失败自动降级哈希 |
| 真实引擎（场景执行/Agent 需要） | `pip install -e ".[upstream]"` 或设 `TCMS_UPSTREAM_DIR` |

### 页面速览
| 路径 | 干什么 |
|---|---|
| `/` | 总览：引擎/资产/AI 后端状态 + 六大资产卡 |
| `/assets` | 报文/信号/FMEA/场景/RTM/功能 六大资产浏览与检索 |
| `/graph` | 图谱工作台（2D/3D、`?focus=F-EBM` 直达） |
| `/scenarios` | 手动/顾问编排 + 真实执行 |
| `/agent` | AI Agent：8 任务 / 自由目标 / 无码症状诊断 / 工具查证 |
| `/faultlab` | 故障动画演示 |

---

## 🧪 测试 / 门禁（数字全部机器可复现）

| 门禁 | 结果 | 说明 |
|---|---|---|
| `pytest -q` | **221 passed + 1 skipped** | 检索 14 golden、诊断 8 golden、26 条对抗集、多轮/澄清/MCP/function-calling 契约… |
| `ruff check src tests` | clean | |
| `pnpm vitest run` | 16/16 | 播放器状态机/3D 布局纯逻辑 |
| `node e2e/graph-interact.mjs` | 5/5 | 图谱单击详情/双击跳转/返回/3D 交互（真浏览器） |
| GitHub Actions | ✅ 绿 | push/PR：pytest+ruff → vitest（双仓自动带真实上游） |

```bash
python -m pytest tests -q                 # 221 passed + 1 skipped
python -m ruff check src tests            # All checks passed
cd web && pnpm test                       # vitest 16/16
```

**诚实边界（项目的性格）**：默认离线确定性检索不叫"语义检索"；6 维评审是 KB 锚定规则而非 LLM 自夸；derived 候选显式标注"仅示意"；无 key/失败一律诚实降级——**不被演示骗、能被数据证**，是设计目标而不是免责声明。

---

## 📁 目录速览

```
tcms-ai-platform/
├── src/tcms_ai_platform/       平台 Python 包
│   ├── core/                   L1 资产模型 + loader（真实资产单一真源）
│   ├── knowledge/              知识底座：图谱/向量/混合检索/弱证据/run 记忆
│   ├── agent/                  Harness/8 任务/诊断/顾问/toolassist(受约束工具)/MCP server
│   ├── domain/                 领域注入(13 系统域/症状/因果表)+ 诊断对抗集
│   ├── faultlab.py             故障演示数据重建器
│   └── server/app.py           FastAPI（单端口托管前端 dist）
├── web/                        React+Vite 前端（2D/3D 图谱、双主题）
├── e2e/                        浏览器走查（graph-interact / main-flow…）
├── scripts/                    规模压测 / 语义通道验证
├── docs/                       审计与口径文档（见下）
└── .github/workflows/ci.yml    push/PR 门禁
```

### 📚 文档索引
| 文档 | 内容 |
|---|---|
| `docs/HARDENING_BACKLOG.md` | 拷问视角补强工单（11/11 完成 + 记录） |
| `docs/TECH_DEPTH_AUDIT.md` | **Agent 技术参与深度逐词自白**（长对话/记忆/工具/MCP/RAG/图谱/评测…）—— 面试/复盘用 |
| `docs/P0-1_TERMINOLOGY_AUDIT.md` | 术语口径审计（措辞不超卖） |
| `docs/CONFIDENCE_CONVENTION.md` | 排序分=依据充分性、非概率 口径 |

---

## 🔗 关系

| 仓库 | 关系 | 说明 |
|---|---|---|
| `tcms-can-test` | **上游引擎** | 真实资产 + 可执行仿真；平台以 `pip install -e ".[upstream]"` 依赖 |
| `tcms-ai-platform` | **本仓库** | 平台 + 生成器（ai-testgen 并入 `ai-testgen/`） |
| `ai-testgen/` | 独立包 | LLM 生成测试 + 变异杀毒/自愈评测的"怎么证明 AI 用例好"答案 |

## License

MIT —— 平台与生成器均 MIT（见 [LICENSE](LICENSE)、[ai-testgen/LICENSE](ai-testgen/LICENSE)）；上游 `tcms-can-test` 亦 MIT。
