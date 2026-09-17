# Q2-Q4 路线图验收台账（ACCEPTANCE LEDGER）

> 对应 `docs/ROADMAP_Q2Q4.md`；每个可自动验证的门禁给出**实测证据**与提交点。
> 结论先行：**步骤 ①-④ 与 ⑤ 的自动化部分全部完成且双侧测试全绿**；
> ⑤-c/d（fresume 式产品化打磨与浏览器 e2e）为 UI/环境类**迭代清单**，不属自动测试门禁，
> 范围与理由见文末「遗留范围」。

## ① 收尾提交 + ai-testgen 回归 ✅

| 验收 | 证据 |
|---|---|
| 上游原子提交 | `d5205a5 feat` + `dddbc02 docs`（main；后随 ③/④ 多笔推进至 v1.12.0） |
| 平台原子提交 | `d3b7f7b feat` + `1018899 docs`（master；后随多笔至 v0.4.0） |
| ai-testgen 独立 venv 回归 | `ai-testgen/.venv`：**136 passed / 28 skipped**（引擎多网段改动后复测仍绿） |
| 版本链 | `/api/health` version=0.4.0 · asset=0.4.0 · engine=1.12.0（loader 默认 platform_version 单一真源 _version） |

## ② Q4 分层有界检索 ✅

| 验收 | 证据 |
|---|---|
| 13 域分区铺全 | domain_systems.json 每 system 增 `domain`；故障 202/202、报文 22/22、信号 116/116、场景 103/103、功能 11/11 全分区（空域仅 10 个基础设施类 SR，断言=精确集合） |
| 检索链路 | 词表路由(13 域) → 图谱定位域兜底（belongs_to 系统边 + 中文字符重合闸门）→ 域内 topk → 无域/不足回退全局（mixed 标注） |
| 指标 | `route_source`(terms/graph/'') + `route_precision`（域查询 =1.0，测试锁定） |
| HTTP 实测 | `空调压缩机过流→routed=[hvac] prec=1.0`；`今天天气不错→unbounded` |
| 测试 | `tests/test_knowledge.py`（含分区/路由/图谱兜底单元） |

## ③ Q2-P-A 第二增量扩库（全部 ✅，v1.12.0 / v0.4.0）

| 子项 | 实测 |
|---|---|
| ③-a FMEA | **66 → 202**（13 系统域全覆盖；12 字段/SIL≥3 detect/inject 完整性断言通过） |
| ③-b 场景 | **25 → 103**（全部可执行、无孤儿故障不变量） |
| ③-c 多网段 | DBC `GenMsgSegment` 单一真源：vehicle 13 / comfort 7 / backbone 1；网段级可调度 vehicle 9.40% / comfort 1.08% / backbone 0.11% **全部可调度** |
| ③-d 四向追溯 | `docs/rtm_4way.md`（52 SR↔场景/模块↔测试↔故障；机器生成 `scripts/gen_trace_4way.py`） |
| ③-e 终扫 | 覆盖率口径 98.00%（2651/55）实测刷新；双仓陈旧数字残留清零 |
| 用例 | 上游 **958 collected（957 passed + 1 skip）** |

## ④ Q3 原子组合器（全部 ✅）

| 子项 | 实测 |
|---|---|
| 组合规划 | `agent/composer.py`：规则点名提取（score≥3.0，≤6，绝不发明）→ steps 兼容 `/api/run/custom` |
| 组合→执行 | `POST /api/agent/composer{run:true}` 真实引擎；测试端到端 all_passed（车门+超速+烟火报警，3/3） |
| 三栏溯源 | provenance：source_asset（字典）/source_system（13 域）/source_agent（规则说明）+ example_scenarios |
| ④-d 多轮 | history 续编（指代式"刚才那个也加上"经前文点名解析；直接点名优先） |
| 诚实边界 | 空目标/无故障 → ok:false 引导（不 422 死路） |

## ⑤ 动画原子化（自动化部分 ✅；⑤-c/d 见遗留范围）

| 子项 | 实测 |
|---|---|
| ⑤-a 域特征演示档 | `faultlab._domain_alarm`：**203/203 故障**均可生成结构化告警（名·子系统域/层·期望处置，诚实标注"示意/巡航基线"） |
| ⑤-b 通道级示例档案 | 8 个 Wave A/B/C 域代表故障 curated（door_open_moving→EB 施加等，语义保守映射）；190+ 由域文案兜底 |
| FaultLab 冒烟 | door_open_moving_eb → emergency_brake；aux_converter_fault_derate → derate（事件时间线完整） |

## 最终门禁（2026-09-08 实测）

| 侧 | 结果 |
|---|---|
| 上游 tcms-can-test v1.12.0 | pytest：**957 passed + 1 skipped（958 collected）**；ruff clean |
| 平台 tcms-ai-platform v0.4.0 | pytest：**134 passed**；ruff clean（src/tests/ai-testgen） |
| ai-testgen | **136 passed / 28 skipped**（独立 venv） |

## 遗留范围（迭代清单，非自动门禁）

- **⑤-c** fresume 式产品化打磨（采纳才应用/版本不落地/截图+粘贴双入口）—— 属前端交互流程，需 UI 人力；自动层已具备三栏溯源/四维雷达等数据基础。
- **⑤-d** 浏览器 e2e 走查 —— 需前端构建 + Playwright 环境，超出本自动回归轮范围；API 级等价冒烟（compose/run、kb route、faultlab demo）已在测试与手动 HTTP 验证覆盖。
- **全 13 域通道级逐条扩谱**：演示叙事已由域特征文案 + 8 个示例档案承载；按需逐域精修属于内容打磨迭代。

---

# Iteration A/B/C 症状多跳诊断验收台账（v0.5.0，工作树未提交）

> 启动包见 `docs/HANDOFF_SYMPTOM_DIAGNOSTIC.md`；三步（A 症状资产 / B 因果边+多跳遍历 /
> C RAG 诊断规划器）在 v0.4.0 之上完成，全部数字由资产文件与测试机器自证。

## A. 症状资产层 ✅

| 验收 | 证据 |
|---|---|
| 症状资产文件 | `src/tcms_ai_platform/domain/data/symptoms.yaml`（随包分发；选择平台侧理由：症状消费方=图谱/规划器全在平台，上游引擎不感知，快照字节级纪律不受影响） |
| 首批症状 | **12 条**：仪表盘闪烁/HMI 无显示/灯具闪烁/客室灯组频闪/大屏花屏/时钟跳变/网络时断时续/SOC 跳变/速度瞬时归零/开门到位灯闪/报警音误响/制动灯异常 |
| schema 合规 | key/name/description/涉及域(13 域标签，主域=向量分区)/hints(2-4 个)/annotation/evidence 全齐 |
| 无孤儿/不冲突 | 症状 key 不与 203 故障键冲突；hints 全部 ∈ 真实故障字典或 13 系统域（`domain/causal.validate_symptom_assets` 强制） |
| 诚实标注 | annotation：real 7 / mixed 5（derived 示意候选显式标注，绝不冒充真实机制） |

## B. 因果边 + 多跳遍历 ✅

| 验收 | 证据 |
|---|---|
| 因果表单一真源 | `domain/data/causal_edges.yaml`：**54 条** = indicates **41**（symptom→fault，12 症状 Σhints）+ causes **13**（fault→fault，如 bms 电流采样漂移→SOC 跳变、24V 充电模块失效→24V 欠压、网关时钟漂移→时间同步丢失） |
| 依据可审计 | real_mechanism **41** / derived **13**；每条带 basis+note；校验断言 symptoms.hints ≡ indicates 目标（防双源漂移） |
| 图谱注入 | enrich 后：symptom 节点 12 + 因果边 54；服务态 651 节点 / 1167 边 / 640 向量文档（基础图仍 517/743/506） |
| 多跳遍历 | `graph.causal_chain` ≤3 跳（症状→indicates→嫌疑故障→causes 反查根因），逐跳带 basis/note；`仪表盘闪烁 → aux_24v_undervoltage → aux_24v_charger_fail/aux_converter_fault` 链在测试中锁定 |

## C. 症状诊断规划器（RAG/harness 层）✅

| 验收 | 证据 |
|---|---|
| 入口 | `agent/diagnoser.py` + `POST /api/agent/diagnose`（message/depth 2~3/max_candidates） |
| 管线 | 症状文本 → kb 检索症状资产(词元重合闸门防噪声) → 图谱候选链 → 诊断步骤建议（验证哪条故障/查哪个报文信号(detect)/用什么现成场景复现）→ 置信度 + 逐条溯源 |
| 诚实边界 | 无命中/空输入 → `no_match=true` + 明确“不确定/需补充 X”，绝不硬答；derived 候选显式标注“仅示意，不可当已确认故障码” |
| 专项回归 | 「仪表盘闪烁但无故障码」：matched、候选非空且全部 ∈ 202 真实字典、建议含 供电(aux: 24V 欠压/电容老化/辅变) 与 显示(network: 司控台屏黑屏) 域候选、evidence 可溯源（函数级 + HTTP 级双测） |
| 能力声明 | `/api/system/status` capabilities.symptom_diagnosis=true；`/api/kb/stats` 增 symptom_causal 台账（12/41/13/54） |

## 最终门禁（Iteration 实测）

| 侧 | 结果 |
|---|---|
| 上游 tcms-can-test v1.12.0 | pytest：**957 passed + 1 skipped（958 collected）**；ruff clean（本迭代未改上游） |
| 平台 tcms-ai-platform v0.5.0 | pytest：**164 passed**（前批 148 + API 契约测试 5）；ruff clean |
| ai-testgen（独立 venv） | 136 passed / 28 skipped（未涉改动，口径不变） |
| 专项回归 | 「仪表盘闪烁但无故障码」函数级 + HTTP 级全绿（非空/可溯源/不编造故障码/含供电+显示域候选） |

## 遗留 / 下一步（非门禁）

- **derived 候选复核**：13 条 derived 因果边为领域工程推断（已在 note 标注理由），建议后续由真实车辆/TCMS 专家逐条复核后提升为 real_mechanism 或删除。
- **LLM 候选内仲裁**：diagnoser 默认确定性规则（use_llm=False）；可按既有 freeform/advisor 模式把 LLM 接入“候选排序/文案润色”（仍只允许候选内仲裁，禁止自由发明故障）。
- **前端接入**：`/api/agent/diagnose` 已就绪，可在 Agent 页/对话流中接入症状诊断卡片（前端本轮未改）。

---

# 体验/资产迭代验收（同 v0.5.0 批次，工作树未提交）

| 用户点 | 交付与实测 |
|---|---|
| ① 场景命名 | 44 个 wave_a/b/c 模板名 → 简短中文名；**104/104 场景补齐 `desc`**（故障序列中文释义，机器生成，审计 0 缺失）；name 唯一、无模板残留；desc 全链（models/loader/列表端点）透出；Scenarios/Agent 结果标题 file→中文名映射 |
| ② FaultLab 提示 | DemoEvent 增 `fault_name/subsystem/domain_zh`（字典派生）；高亮点与 chip **显示具体故障名 + 「【系统域 · 子系统】」定位**（不再是“系统异常”）；注入瞬间横幅“哪里+什么”；**点击高亮点/chip 弹出异常说明**（等级/现象/检测语义，原生 tooltip 悬停） |
| ③ 图谱默认+交互 | `GET /api/kb/overview`（13 系统+11 功能+代表故障 = **56 节点/65 边**）进页即显；2D/3D 节点单击详情/双击跳转；3D 滚轮缩放+适配+停转 3s 自动恢复自转；修复 `?focus=` 直达前缀拼坏 |
| ④ 遗留盘点/资产健康 | 见上文“遗留/下一步”与下方资产健康快照；本批新落地：**症状诊断前端卡片**（Agent 页，全离线确定性） |
| ⑤ 迭代清单续做 | 场景 desc 103/103；症状诊断卡接入 Agent 页；LLM 候选仲裁开 key、真 3D 平滑、e2e 主流程、检索/Agent 评测等已在**文末“路线图最终验收”**落地（remaining：derived 复核、fresume/e2e 更细粒度、three/BGE 可选增强） |

**最终门禁（本批实测）**：平台 164 passed / ruff clean；上游 957 passed + 1 skip / ruff clean；前端 `tsc -b` 零错误 + vite build 通过；本地服务 127.0.0.1:8000 已重启为 v0.5.0 并验证 `/api/health`、`/api/kb/overview`、新 bundle 静态托管。

## 资产健康快照（2026-09 实测）

- 故障字典 202（hints/场景全部锚定）；场景 103（name 全非空唯一，103/103 均带故障序列 desc）；症状 12 + 因果边 54（indicates 41/causes 13；real 41/derived 13）；DBC 22 帧/116 信号/3 网段（vehicle 13/comfort 7/backbone 1）。
- 平台内置快照与上游场景集合一致（103=103）；服务态 KB 651 节点/1167 边/640 向量文档。
- 已知待补足（非门禁）：derived 因果边待领域复核；⑤-c/d fresume 产品化打磨与更细粒度浏览器用例仍列迭代清单；“真三维 three/R3F”与“真神经 embedding(BGE)”作为可选增强（现以等价平滑 3D 与 BM25 混合+可插拔适配兜底，见下方路线图验收说明）。

---

# 工程化/面试技术栈路线图最终验收（P0→P2，同 v0.5.0 批次，工作树未提交）

> 路线图目标见 goal；本表 = 逐项交付与机器证据，红线合规在文末。

| 项 | 交付 | 证据/门禁 |
|---|---|---|
| P0-0 真 LLM 链路 | Aliyun key 存本机 settings（不入库不打印）；真实模型列表 249；真 LLM 仅候选内润色/仲裁 | `/api/llm/models` OK；advisor/diagnose live `llm_generated=true`；建议使用后轮换 key |
| P0-1 API 契约测试 | `tests/test_api_contracts.py`（+5）锁定 overview/场景 name+desc/FaultLab 事件字段/diagnose 结构/kb 台账；顺修 faultlab domain_zh bug | 平台 164 passed / ruff clean |
| P0-2 播放器状态机 + vitest | `web/src/lib/playerState.ts` 纯函数（advancePlayback/eventWindow/buildSpans…）组件接入；vitest 基建 | `pnpm test` **16/16** |
| P1-1 检索工程化 | `lexical.py` BM25 + `retrieve_hybrid`（RRF）+ `/api/kb/search` 切混合；golden 评测集 14 条 | 混合 **14/14**、纯向量 ≥13/14 防回退 |
| P1-2 图谱可查询/可迁移 | `shortest_path`（带依据边）+ `/api/kb/path` + `graphio` 无损导出 + 评估文档 | live 2 跳证据链；导出计数 == stats |
| P1-3 Agent 评测复用 | `agent/evals.py` + agent_golden（诊断 8/8、自由解析 5/5、摘要指标）；修跨域召回 | tests 全绿 |
| P1-4 Playwright e2e | `e2e/main-flow.mjs`（真实 chromium）主流程 5 断言 | `npm run smoke` **5/5 PASS** |
| P2-1 3D 平滑（等价平滑 3D） | `lib/graph3d.ts` 几何/缓动 + 3D 适配缓动过渡 | vitest +5；e2e 3D 断言通过 |
| P2-2 症状诊断 LLM 仲裁 | Agent 页开关 + 后端候选内仲裁（禁止新键、失败回退） | fake/回退单测 + 真 key live true |
| P2-3 ai-testgen 真 LLM 复测 | `.venv` 补 `.[llm]`；真 LLM 臂跑通 qwen-plus | parse .667/compile 1.0/exec 1.0；报告 json |

**诚实说明（非掩盖）**：
- “真 embedding(BGE 类)”未引入需联网下载的模型权重：以 **BM25 词法 + 向量 RRF 混合**落地并保留 `Embedder` 可插拔适配（离线优先纪律），神经 embedding 列可选增强；
- “真 3D(R3F/three)”未引入新依赖：按目标允许的“**等价平滑 3D**”落地（几何/缓动纯函数 + 平滑适配/缩放/自转恢复）；
- deepseek-v4-pro-0813 对 ai-testgen 超长提示在百炼端点超时 → 用 qwen-plus 完成复测并记录限制。

**最终门禁（2026-09 实测，收口前重跑）**：平台 **164 passed** / ruff clean；上游 957 passed + 1 skip / ruff clean；前端 tsc 0 错误 + vite build 通过；vitest 16/16；浏览器 e2e 5/5；本地服务 v0.5.0 运行中。
**红线合规**：无 commit；key 不入库不打印；derived/示意逐条标注；计数随改随同步（README/CHANGELOG/ACCEPTANCE/PROJECT_ARC/ROADMAP 全链 164）。
