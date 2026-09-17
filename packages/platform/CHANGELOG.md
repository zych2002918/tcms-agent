
# Changelog — tcms-ai-platform

版本单一真源在 `src/tcms_ai_platform/_version.py`；本文件记录用户可见变更。

## 0.5.0 (2026-09)

- **症状多跳诊断迭代（Iteration A/B/C，工作树未提交）**：
  - A 症状资产 `src/tcms_ai_platform/domain/data/symptoms.yaml`：**12 条**（仪表盘闪烁/HMI 无显示/灯具闪烁/客室灯组频闪/大屏花屏/时钟跳变/网络时断时续/SOC 跳变/速度瞬时归零/开门到位灯闪/报警音误响/制动灯异常），hints 全部锚定上游 202 真实故障键与 13 系统域（annotation real 7 / mixed 5）。
  - B 可审计因果表 `domain/data/causal_edges.yaml`：**54 条边**（symptom -indicates-> fault 41 / fault -causes-> fault 13；real_mechanism 41 / derived 13 逐条标注）；图谱 `causal_chain` ≤3 跳有向遍历，逐跳带 basis/note。
  - C 症状诊断规划器 `agent/diagnoser.py` + `POST /api/agent/diagnose`：无码症状 → kb 检索症状资产 → 图谱候选链 → 诊断步骤建议（置信度/验证动作/场景复现/证据溯源）；无命中或证据不足 → 明确“不确定/需补充”，绝不编造故障码（derived 候选显式标注仅示意）。
  - 注入后服务态 KB：**651 节点 / 1167 边 / 640 向量文档**（含 12 症状节点+文档与 54 因果边）；基础图谱不变（517/743/506）。
  - 专项回归「仪表盘闪烁但无故障码」双层通过（函数+HTTP：非空、可溯源、不编造故障码、建议含 供电 aux 与 显示 network 域候选）。
- 验证：平台 **164 passed** / ruff clean；上游 tcms-can-test 957 passed + 1 skip（未改动）；ai-testgen 136 passed / 28 skipped（未涉，口径不变）。
- 红线：症状/因果数量经 `domain/causal.validate_symptom_assets` 机器自证；域词汇与 DBC 段单一真源未动。

## 0.5.0 体验/资产迭代（同批次，2026-09）

- **场景命名释义（用户点 #1）**：44 个 `wave_a/b/c` 模板名（“Wave X 域内多故障编排：键列表”）改为**简短中文释义**，并为全部 103 个场景补齐 `desc` 故障序列释义（真实故障中文序列，机器生成）；`desc` 全链建模（models/loader/`/api/scenarios`、`/api/faultlab/scenarios`）并在 Scenarios/Agent 结果标题做 file→中文名映射（含 `?focus=` 直达修复）。
- **FaultLab 异常提示可读化（用户点 #2）**：演示事件新增 `fault_name/subsystem/domain_zh`（真实故障字典派生）；**列车高亮点与下部 chip 不再显示笼统“系统异常”**——每点显示具体故障名并带「【13 系统域 · 子系统】」前缀；注入瞬间出现横幅“哪里 + 什么”；**点击高亮点/chip 弹出该故障的异常说明**（等级/现象/检测说明，悬停也有原生 tooltip）。
- **症状诊断卡片接入 Agent 页**（延续迭代清单）：`/api/agent/diagnose` 前端卡片（输入“仪表盘闪烁但无故障码”→ 症状资产 + 候选卡 + derived 标注 + 复现场景 chips），全离线确定性。
- **知识图谱默认视图与交互（用户点 #3）**：新增 `GET /api/kb/overview` 默认基础骨架（13 系统 + 11 功能 + 每功能代表故障 = **56 节点 / 65 边**），进页即展示无需先搜索；2D/3D 节点**单击看详情、双击以其为中心跳转**；3D 新增滚轮缩放、一键适配（视角重置）与“停转 3 秒自动恢复待机自转”。
- 验证：平台 **164 passed** / ruff clean；上游 957 passed + 1 skip / ruff clean；前端 `tsc -b` 零错误 + `vite build` 通过（dist 新哈希）。

## 0.5.0 工程化/面试技术栈落地（同批次，2026-09）

- **API JSON 契约测试（P0-1）**：`tests/test_api_contracts.py`（+5，平台 164 passed）锁定 overview 骨架 / 场景 name+desc / FaultLab 事件“哪里+什么”字段 / diagnose 响应结构 / kb 症状因果台账；顺修 faultlab `domain_zh` 注入 bug。
- **FaultLab 播放器状态机化（P0-2）**：`web/src/lib/playerState.ts` 纯函数（钳位 / `advancePlayback` 确定性推进 / `eventWindow` / `buildSpans`+`activeSpanKeys` 故障窗口），组件 rAF / 事件窗 / 高亮集合全部改为消费纯函数；**vitest 单测基建**（`web/src/lib/*.test.ts`，16 用例全绿，`cd web && pnpm test`）。
- **LLM 真实链路（P0-0）**：Aliyun key 存本机 settings（不入库/不打印），`/api/llm/models` 拉取 249 模型，真实 LLM 仅做候选内文案润色验证通过（key 建议使用后轮换）。
- **混合检索 + 检索评测（P1-1）**：`knowledge/lexical.py`（字符/词 BM25，k1=1.5 / b=0.75，域分区过滤）+ `retriever.retrieve_hybrid`（向量 × BM25 **RRF 融合**，响应与 retrieve 同构）；`/api/kb/search` 切到混合通道；**golden 评测集** `domain/data/retrieval_golden.yaml`（14 条真实期望 id）+ `knowledge/golden.py` 评测器 —— 混合通道 14/14、纯向量 ≥13/14 防回退门禁。
- **证据图可查询/可迁移（P1-2）**：`graph.shortest_path`（BFS 最短路，逐边带 kind/basis/note，按行走方向定向）；`POST /api/kb/path` 证据路径端点（不可达 → 诚实 found=false）；`knowledge/graphio.export_graph_json` **无损导出**（节点 props + 边依据，计数 == stats 测试锁定）；评估与迁移方案落文 `docs/GRAPH_ENGINE_EVALUATION.md`（现内存图正确、触发迁移判据 + Kuzu 首选方案 + 等价性门禁）。
- **Agent 评测复用（P1-3）**：`agent/evals.py`（诊断 golden / 自由解析 golden / harness 摘要指标）；`domain/data/agent_golden.yaml` —— 诊断 8/8、自由解析 5/5（全部真实期望、不编造逐条校验）；顺修症状匹配跨域召回（域词“客室”不再把 light 域症状文档挡在 hvac 分区外 → 症状匹配走全局检索）。
- **浏览器 e2e 主流程（P1-4）**：`e2e/main-flow.mjs`（playwright-core + 本机缓存 chromium，真实浏览器机器断言）——图谱默认骨架 / **3D 视图切换与控件** / FaultLab URL 直达自动播放并出高亮 / Agent 症状诊断卡输出候选；`cd e2e && npm run smoke`，5/5 PASS，失败非 0 退出（`e2e/README.md`）。
- **3D 平滑增强（P2-1，等价平滑 3D，不引 three 依赖）**：`web/src/lib/graph3d.ts` 纯几何/缓动（费波那契球面、透视投影、相机缩放、easeInOutCubic、approach）+ vitest（+5，共 **16 用例**）；3D「⤢ 适配」改为 **~420ms 缓动过渡**到默认视角；滚轮缩放沿用 clamp 区间；为将来换 R3F 保留同一语义。
- **症状诊断 LLM 候选内仲裁开关（P2-2）**：Agent 页“症状诊断”卡片加 **LLM 候选内仲裁**勾选（需已配置 key）；后端 `diagnose_symptom(use_llm=True)` 只在**候选故障键内**让 LLM 挑选/重排（原样提示 + 解析双重约束：非候选键一律丢弃；失败/无 key 自动落回规则排序，`llm_generated` 如实标注）；真实 Aliyun 链路 live 验证 `llm_generated=true`。
- **ai-testgen 真 LLM 复测（P2-3）**：`ai-testgen/.venv` 补 `.[llm]`，真 LLM 臂全链路（生成→parse→compile→真实 pytest）跑通：`qwen-plus` 3 例 → parse_rate 0.667 / compile 1.0 / exec_pass 1.0（报告 `ai-testgen/docs/reports/llm_retest_2026.json`）；诚实记录：`deepseek-v4-pro-0813` 对超长提示在该端点超时（环境限制），LLM 样本偏小、未解析 1 例计入 parse_rate 不掩盖。平台 **164 passed**。

## 0.4.0 (2026-09-08)

- **Q2-P-A 第二增量收口（上游 v1.12.0）**：FMEA 202 / 场景 103 / 958 用例 / 多网段 GenMsgSegment / RTM 四向追溯（doc: docs/rtm_4way.md 上游）；KB 基础图谱 517 / 向量 506。
- **Q2-P-A Wave A 同步（上游 97 FMEA / 75 场景 / 902 用例）**：快照 faults+16 场景同步；KB 基础图谱 384 节点 / 向量 373 文档；计数测试/README 全链更新（全量 122 passed）。
- **Q2-P-A Wave B 同步（上游 135 FMEA / 89 场景 / 930 用例）**：快照 faults+14 场景同步；KB 基础图谱 436 节点 / 向量 425 文档；计数测试/README 全链更新（全量 122 passed）。
- **Q2-P-A Wave C 同步（上游 202 FMEA / 103 场景 / 958 用例 —— ③-a/b 目标达成）**：快照 faults+14 场景同步；KB 基础图谱 517 节点 / 743 边 / 向量 506 文档；/api/kb/nodes 增加显式 limit 参数（默认 200 浏览上限，防计数误读）；计数测试/README 全链更新（全量 122 passed）。

## 0.3.0 (2026-09-08)

- **Q2-P-A 资产真实化第一增量**（上游引擎同步 v1.11.0）：
  - 资产扩容：**8 → 22 报文 / 38 → 116 信号**（+14 帧覆盖 HVAC/PIS/照明/烟火/辅助变流/ATO/走行部/后门/网关/防滑/牵引变流/司控台/充电机，周期分级 25–500ms）；**26 → 66 条 FMEA**（13 系统域 × 16 子系统）；**25 → 59 场景**（剧本族组织）；**SR-01~18 → SR-01~52**；设备 5 → 11。
  - 被测功能 4 → **11**（+F-TRAC/F-BRAKE/F-HVAC/F-PIS/F-FIRE/F-PWR/F-BOGIE，锚定新 RTM/DBC/故障键，漂移即失败）。
  - 列车系统分类框架 6 → **13 系统域**（S1000D 对齐）：图谱 239 → **337 节点 / 504 边**，向量 234 → **326 文档**，全部故障 belongs_to 某系统。
  - Agent 自由目标：未命中文案故障数改为动态派生（原硬编码"22"修复）；同分平局倾向更长更具体的名称命中（多区烟火报警 > 烟火报警）；**66/66 故障名均可被确定性规则命中**（新增不变量测试）。
  - 内置快照 `_assets` 四件套同步扩容（新人 clone 单仓库即可用）。
- 验证：平台 `116 passed` / ruff clean；上游 `870 collected（869 passed + 1 skip）` / 覆盖率 98.00%。

## 0.2.0 (2026-09)

- **版本语义拆分**：`/api/health` 现在返回双段版本——`version`(平台自身) / `engine_version`(上游 tcms 引擎)；总览不再把平台版本误标为引擎版本。
- **总览状态条**："平台运行中 · v0.2.0 · 引擎 vX / 引擎未接入"，区分平台与上游引擎。
- 从 0.1.0 bump（前 29 commits 未动版本号）。

### 0.1.0 里程碑回顾（前 29 commits，版本号当时未演进）

- P1–P5：资产模型/知识底座/前端 MVP/Agent Harness/打磨（详见 git 历史与 docs/PLAN 系）。
- r2/r3：手动编排、AI 编排顾问、动画资产化、双主题、图谱 2D/3D、ai-testgen 并入、RAG 无匹配建议。
