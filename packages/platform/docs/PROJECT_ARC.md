# TCMS × AI：从零到开源的开发路径复盘（PROJECT ARC）

> 本文档回答三个问题：
> ① 这个项目一路走来经历了什么、构建了什么（**简历素材**，可被面试追问）；
> ② 我们按什么目的/思路/方式/准则/手段/结果在推进（**方法论**，可复用于其他项目）；
> ③ 文件与记忆怎么分层、怎么索引（**秩序**，让任何一次续写都能快速恢复上下文）。
>
> 定位：平台仓库内的"元文档"，与 README（对外叙事）互补——README 讲"是什么"，
> 本文档讲"怎么走到这里、为什么这样走、下次怎么复用"。

---

## 0. 一句话总结

> 从一份 802 条手写用例的 TCMS（列车网络控制系统）CAN 测试工程出发，我们依次构建了
> "真实资产模型 → 知识底座(图谱+向量+GraphRAG) → Agent 工作流(检索→执行→自证→沉淀) →
> 本地 Web 平台(双主题/图谱2D+3D/故障动画/AI 编排顾问)"，并把两条独立能力线合并为一个
> GitHub 仓库——最终交付物是"**AI 测试工程师能干活、能自证、看得见**"的可复现平台。

---

## 1. 目的（Why：为了回答什么问题）

| 层次 | 目的 |
|---|---|
| 行业问题 | LLM 生成的测试用例**如何证明是"好的"**？——业界主流(覆盖率循环)会放过真实 bug（见 arXiv:2412.14137 批评），我们需要"真实预期裁决 + 变异杀毒"的正当性 |
| 项目问题 | 从 TCMS 真实资产到"AI 会写、会跑、能自证、看得见"的**最小可信闭环**，而不是堆点状 demo |
| 个人目标 | 让一个可复现项目同时命中多条岗位叙事：**AI 测开（测试用例自动生成/LLM-as-judge/评测体系）+ Agent 工程化（Harness/自愈/轨迹审计）+ 安全关键域测试（CAN/嵌入式）** |

北极星一句话：**做一个 AI 测试工程师能真正干活、能自证、看得见的 TCMS 列车控制软件测试平台。**

---

## 2. 思路（How：问题怎么拆）

核心认知：**不扩广度，做深纵深，把"AI 参与"挂上可评测的钩子。**

```
真实资产(已有,不必等)  →  资产模型化(L1)   →  知识底座(L4)  →  Agent 编排(L5)  →  平台外壳(L6)
   tcms-can-test         列车视角 schema      图谱+向量+RAG     检索→执行→自证     本地 Web
   802 用例/98%覆盖      AI/UI 共同事实面       组织记忆         6维评审/反思自愈     一屏看见价值
```

三条决策线（见仓库内 ADR 式记录）：
1. **TCMS 做深不做宽**：不加总线种类/不扩仿真，现实贴合靠"资产模型真实"（DBC/faults.yaml/RTM/领域知识 JSON 三件套）。
2. **AI 能力一律挂评测**：每个 Agent 角色/生成源先定义任务集+评分+口径，否则退回"让 LLM 说话"。
3. **每个里程碑交付"用户可感知的完整切片"**，改动必须服务某个切片验收，防东拼西凑。

---

## 3. 方式与手段（With what：积木与工作方法）

### 3.1 方式：三仓一链，逐步收拢

| 阶段 | 仓库/产物 | 干了什么 | 手段 |
|---|---|---|---|
| 地基 | `tcms-can-test` v1.10.0 | 802 手写 pytest / 98% 覆盖 / 26 FMEA / 25 事件式 YAML 场景 / RTM SR-01~18 / 多网段拓扑 / CAN 错误状态机 | DBC+cantools+python-can+虚拟总线；数字机器自证(gen_badges 铁律) |
| AI 积木 | `tcms-ai-testgen` (26 commits) | 生成器→**受约束 execution DSL**→真实 pytest→变异杀毒/反思自愈/双 judge | 白名单 DSL(pydantic) + oracle 派生期望 + RAG 金标 635 条 + diff 门禁防假自愈 |
| 平台化 | `tcms-ai-platform` (29 commits) | L1 资产模型 + FastAPI 服务层 → 知识底座 → React MVP → Agent Harness → 打磨(双主题/3D 图谱/动画资产化/AI 顾问) | FastAPI 单端口托管 dist；图谱手写力导向零依赖；HashedEmbedder 离线可复现 |
| 收拢 | 本会话(2 commits) | **testgen 并入 platform** 为 `ai-testgen/`；README 一体叙事；双主题+图谱 2D/3D；自由 Agent 无匹配→RAG 候选 | git 子树式拷贝(去 git/venv/缓存) + 双 README 迁移指引 |

### 3.2 手段清单（可复用工具箱）

- **机器自证**：所有展示数字必须由真实资产/执行派生，禁止手抄；对拍测试防漂移（oracle↔上游常量逐键对比）。
- **诚实边界**：口径文档权威；分母含残缺样本；示意 vs 真实逐级标注(source{kind,ref,desc})；失败实验也存档。
- **三层配置**：环境变量 > 本机设置文件(settings.json，key 只落本机) > 内置快照——新人零配置可跑。
- **可插拔抽象**：AgentBackend(Mock/LLM 一键切换)、Embedder(离线哈希→可换真向量)、前端组件语义化。
- **浏览器走查**：playwright+Edge 脚本化 e2e（主题/图谱/Agent 建议流），改完 UI 必跑。
- **门禁**：pytest 全绿 + ruff clean + cov 阈值；LLM 非确定性用 mock/录播测（CI 可跑）。
- **GBK 陷阱已踩平**：Windows PowerShell 发中文 body 会 GBK 乱码成 `?`（请求必须 UTF-8 bytes）；读 UTF-8 中文文件用 read 工具而非 Get-Content；.gitignore 曾因 GBK 注释乱码需重写为 UTF-8。

### 3.3 会话内量化证据（浏览器/HTTP 实测）

- 图谱 2D：滚轮缩放以光标为中心、拖拽平移、双击适配；3D：费波那契球面+透视投影、拖拽旋转/自转。
- 主题：dark/light 切换 + localStorage + 后端 theme 字段持久化 roundtrip（HTTP 200）。
- 自由 Agent 无匹配：`POST /api/agent/free` 现返回 200 + no_match + RAG 候选(门→door_fault/door_sensor_noise)，e2e 3/3。
- 门禁终态：platform 111 passed / ruff clean / vite build 绿；ai-testgen 119 passed(+2 数据漂移)。

---

## 4. 准则（铁律，可迁移）

1. **不扩仿真广度**——现实贴合靠资产模型/需求锚点真实，不加总线与仿真器堆砌。
2. **AI 能力必须挂评测**——每个 Agent 角色/生成源先定义任务集+评分+口径，否则退回"让 LLM 说话"。
3. **展示数字机器自证**——禁止手抄任何数字。
4. **改动服务于某个验收切片**——切片未过前不横向加功能；不做细微调试死循环。
5. **成本受控 + 敏感不泄露**——LLM 按需（离线 mock 优先，诚实标注）；key 只走环境变量/本机文件，绝不入库/入响应/入日志。
6. **先规划后动手**——P0 裁判文档(北极星)先立，改什么都要能指回它。
7. **每阶段先自测后回报**，沿用 orders/STATUS 回报纪律。

---

## 5. 结果（What we got：可对外主张的事实）

### 5.1 资产与能力现状（2026-09-07 实测）

| 资产 | 数量 | 来源 |
|---|---|---|
| 报文 / 信号 | 8 / 38 | DBC |
| 故障(FMEA) / 场景 | 26 / 25 | faults.yaml / scenarios/ |
| 需求(RTM) / 功能 / 设备 | 18 / 4 / 5 | rtm.csv / 手工锚定 |
| 图谱节点 / 边 | 239 / 385 | 资产 + 领域知识(EBM/网络/安全/列车系统分类) |
| 向量文档 | 234 | 资产 + 领域(离线 HashedEmbedder) |
| Agent 内置任务 | 8 | 真实故障锚定防漂移 |

### 5.1b Q2-P-A 资产真实化第一增量（2026-09-12 实测，当前态）

> 见 `docs/NEXT_PHASE_BLUEPRINT.md` P-A 落地：资产从"教学演示级"推进到
> "13 系统域代表性子集"，图谱/向量/Agent 命中空间同步放大。全链数字机器自证。
> **本节为当前实测态，数字随上游 tcms-can-test 增长同步刷新；§5.1 保留为起点快照。**

| 资产 | 数量 | 来源 |
|---|---|---|
| 报文 / 信号 | 22 / 116 | DBC（+14 帧：HVAC/PIS/照明/烟火/辅助变流/ATO/走行部/后门/网关/防滑/牵引变流/司控台/充电机；周期分级 25–500ms） |
| 故障(FMEA) / 场景 | 203 / 104 | faults.yaml（13 系统域 × 16 子系统）/ scenarios/（剧本族） |
| 需求(RTM) / 功能 / 设备 | 52 / 11 / 11 | rtm.csv（SR-01~52）/ 手工锚定 / DBC 节点 |
| 图谱节点 / 边 | 基础 519 / 748；服务态(含 enrich+症状/因果) 655 / 1176 | 资产 + 领域知识(EBM/网络/安全/13 系统域) + 症状 12 + 因果边 54 |
| 向量文档 | 基础 508；服务态 644 | 资产 + 领域(离线 HashedEmbedder) + 12 症状文档 |
| 症状资产（无码） | 12 | symptoms.yaml（hints 全锚真实故障键/13 系统域；annotation real 7/mixed 5） |
| 因果边（诊断） | 54（indicates 41 / causes 13） | causal_edges.yaml（real_mechanism 41 / derived 13，逐条 basis+note） |
| 图谱默认骨架（overview） | 56 节点 / 65 边 | GET /api/kb/overview：13 system + 11 function + 每功能代表故障 |
| 场景中文名 / desc | 104 name 唯一；44 个 wave_* 模板名已改短并补 desc | scenarios/*.yaml + Scenarios/Agent 结果标题映射 |
| Agent 内置任务 | 8 | 真实故障锚定防漂移（自由目标规则 + RAG 澄清，见 freeform/advisor） |

### 5.2 工程与验证

- 平台 v0.5.0：`221 passed + 1 skipped` / ruff clean；`ai-testgen/`：`165 passed`（离线 mock 模式）；上游 tcms-can-test v1.12.0：`959 passed + 1 skip`。
- GitHub：`tcms-ai-platform` 43 commits(含并入)、`tcms-ai-testgen` 迁移指引 28 commits、`tcms-can-test` v1.12.0。
- 双仓库合一：独立包结构保留(`pip install -e "./ai-testgen[test]"`)，README 一体叙事。

### 5.3 简历可主张（三句话叙事，附追问弹药）

1. **从 0 建了一条"LLM→受约束 DSL→真实 pytest→变异杀毒→反思自愈→双 judge"的 AI 测试用例工厂流水线**，用量化证据（parse/compile/exec/kill_rate/self-heal）证明"AI 写的测试好不好"，规避了"只求通过+覆盖会放过真 bug"的业界陷阱。
2. **把它做成了一个本地可复现的 TCMS 测试平台**：真实资产模型（22 报文/116 信号/203 FMEA/104 场景/52 SR/11 功能/13 系统域/12 症状资产）→知识图谱(基础 519、服务态 655 节点,含 13 系统分类树与 54 条可审计因果边)+GraphRAG+症状多跳诊断→Agent Harness(检索→真实执行→6 维语义评审→轨迹可审计)→Web 端到端可见（双主题/图谱 2D+3D/故障动画逐帧溯源）。
3. **工程纪律全程机器自证**：数字全由真实资产派生、任务库锚定真实故障字典(漂移即失败)、LLM 决策可落回离线 Mock、API key 永不入库——项目本身先有最好的测试。

---


> **Iteration A/B/C 症状多跳诊断（v0.5.0，2026-09，工作树未提交）**：12 症状资产 +
> 54 条因果边（indicates 41 / causes 13；real_mechanism 41 / derived 13）注入图谱；
> 服务态 **655 节点 / 1176 边 / 644 向量文档**（基础图仍 519/748/508）；多跳遍历 ≤3 跳
> （症状→嫌疑故障→causes 反查根因，逐跳 basis/note）+ `POST /api/agent/diagnose`；
> 「仪表盘闪烁但无故障码」专项回归通过（非空/可溯源/不编造故障码/含 aux+network 域候选）；
> 平台 **221 passed + 1 skipped** / ruff clean；上游 959 passed + 1 skip（本迭代未改上游）。

## 6. 文件层次索引（File Index：在哪里找什么）

```
objects/（根，gitlink 聚合 + 导航）
├── 00_工作区导航索引.md           ← 顶层唯一入口：所有项目/素材/交接一表定位
├── TCMS-AI_北极星规划.md          ← TCMS 线裁判文档（P0，决策/里程碑/红线）
├── tcms-ai-testgen_内容评估与市场对标报告.md  ← 行业对标 + JD 映射（面试弹药）
└── tcms-ai-platform/（独立 git）
    ├── README.md                  ← 对外一体化叙事（是什么/怎么跑/架构）
    ├── docs/PROJECT_ARC.md        ← 本文档（路径复盘 + 可复用方法论 + 索引）
    ├── docs/frontend-design-skill.md / redesign-plan.md  ← 前端设计系统/改造计划
    ├── src/tcms_ai_platform/
    │   ├── core/                  L1 资产模型(models/loader/settings/sources)
    │   ├── knowledge/             graph/vector/retriever(图谱+向量+GraphRAG+sink)
    │   ├── agent/                 tasks/harness/freeform/advisor/reviewer/llm_backend
    │   ├── faultlab.py            故障演示数据重建器(事件时间线+通道曲线+诚实溯源)
    │   ├── domain/data/           领域知识 JSON(EBM/网络/安全/13 系统域) + symptoms.yaml/causal_edges.yaml + enrichment
    │   └── server/app.py          FastAPI 全端点
    ├── web/                       React+Vite+Tailwind v4（双主题；图谱页=GraphWorkspace.tsx）
    ├── ai-testgen/                （原独立仓库并入）生成器全套 + docs/reports/*.json 量化报告
    ├── tests/  e2e/  packaging/   平台测试 / 浏览器走查 / exe 打包
    └── .env.example               （UTF-8，变量清单）
```

### 6.1 关键文件速查

| 想找 | 去哪 |
|---|---|
| 对外介绍平台 | `tcms-ai-platform/README.md` |
| 里程碑/裁决/红线 | objects 根 `TCMS-AI_北极星规划.md` |
| 行业对标/面试映射 | objects 根 `tcms-ai-testgen_内容评估与市场对标报告.md` |
| 本路径复盘/方法论 | `tcms-ai-platform/docs/PROJECT_ARC.md`（本文档） |
| 知识底座怎么建 | `src/tcms_ai_platform/knowledge/*` + `domain/` |
| Agent 流水线怎么跑 | `src/tcms_ai_platform/agent/harness.py`（retrieve→plan→exec→verify→reflect→report） |
| 自由目标怎么理解 | `agent/freeform.py` + `server/app.py` 的 `/api/agent/free` |
| 症状多跳诊断怎么跑 | `domain/causal.py`（症状资产+因果表 装载/校验/注入）+ `agent/diagnoser.py` + `/api/agent/diagnose` |
| 动画怎么资产化 | `faultlab.py`（`_demo_from_step_sources`）+ `web/src/pages/FaultLabPage.tsx` |
| 图谱 2D/3D | `web/src/pages/GraphWorkspace.tsx`（GraphCanvas2D/3D） |
| 量化证据报告 | `ai-testgen/docs/reports/*.json`（README 每个数字可复现） |
| 环境变量清单 | `.env.example` + 设置页「环境变量键值表」 |

---

## 7. 记忆层次索引（Memory Index：知识怎么分层）

| 层 | 载体 | 存什么 | 怎么读/写 |
|---|---|---|---|
| L0 会话上下文 | 各次对话 | 当次任务、临时验证、中间决定 | 不跨会话，靠下两层接续 |
| L1 项目文档 | 各仓库 docs/ + 根规划 | **稳定事实**：架构/数字/边界/红线/验收（本文档属于此层） | 用文件路径定位，改完即提交 |
| L2 图记忆(DSH GM) | `%USERPROFILE%\.dsh\graph-memory` | **跨项目可复用知识**：完成态基线、踩坑修复(SKILL)、可迁移方法(EVENT/TASK) | gm_search 召回 / 关键结论后 gm_record 沉淀 |
| L3 工作区导航 | objects 根 `00_工作区导航索引.md` | 项目清单 + 上游素材 + 交接（跨工作区入口） | 改项目状态/新增仓库必更新 |

### 7.1 本项目已沉淀进图记忆的条目（避免重复劳动）

- `tcms-platform-recon-baseline`(TASK)：2026-09 实测基线——26故障/25场景/18需求/8报文/38信号/4功能/5设备；图谱239/385(含6系统族)；向量234；8 Agent 任务硬编码防漂移；FaultLab `_profiles()` 与前端 `FAULT_SPOT_META` 双表无同步校验；testgen 的 venv editable 指向已删除 D: 路径(需 PYTHONPATH=src)。
- `tcms-gh-push-and-clone-verified`(EVENT)：GitHub 推送/克隆验证全链路通过的方法（git credential fill 取 token → API → push → clone 验证）。
- 其他可复用：`glob-file-search` / `read-file-utf8-windows` / `dump-trial-traces`(SUMO 线) 等工具型 SKILL。

### 7.2 续写本项目/复用本项目时的开场协议

1. 读 objects 根 `00_工作区导航索引.md` 定位三仓库与裁判文档；
2. 读 `TCMS-AI_北极星规划.md` 第 5-6 节（里程碑状态 + 红线）；
3. 若改 platform 代码：`git -C tcms-ai-platform status` 确认干净 + 读相关模块 docstring；
4. 若跑测试：`PYTHONPATH=src .venv/Scripts/python -X utf8 -m pytest tests -q`（platform）；ai-testgen 同理加其 src；
5. 记忆召回：gm_search("tcms-platform-recon-baseline") 拿最新实测基线，避免重新侦察。

---

*本文档随项目推进更新；修订记录见 git。与 `README.md`（对外叙事）、`TCMS-AI_北极星规划.md`（裁判）三角互证。*