# 后续分步执行计划：Q2-Q4 路线图（2026-09-08 起）

> 承接 `NEXT_PHASE_BLUEPRINT.md`（裁判依据）与已完成的 Q2-P-A 第一增量
> （22 报文 / 116 信号 / 66 FMEA / 59 场景 / 52 SR / 11 功能 / 13 系统域，
> 双侧测试全绿）。本文件是**可执行的逐步清单**：每步独立可验收，逐项打勾。

## ① 收尾提交 + ai-testgen 回归（✅ 完成 2026-09-08）

- [x] a. 上游 `tcms-can-test` 分批原子提交（v1.11.0，main）：
      1) `d5205a5 feat(q2-p-a)`：DBC+FMEA+场景+RTM+simulator 全帧 + 配套测试
      2) `dddbc02 docs(v1.11.0)`：文档数字同步 + CHANGELOG + _version
- [x] b. 平台 `tcms-ai-platform` 分批原子提交（v0.3.0，master）：
      1) `d3b7f7b feat(q2-p-a)`：13 系统分类法 + loader 11 功能 + 快照 + freeform 66/66 + 版本链修复
      2) `1018899 docs(v0.3.0)`：README/CHANGELOG/PROJECT_ARC + 本路线图
- [x] c. ai-testgen 子包独立 venv（`ai-testgen/.venv`）全量回归：**136 passed / 28 skipped**（skip=真 LLM 类）
- [x] d. 服务重启验证：health/stats/system/engine 全绿（平台 0.3.0 · 引擎 1.11.0 · 图谱 459·738 · 向量 448，见下"引擎状态"）

**验收**：上游 870 collected 绿；平台 119 passed 绿；ai-testgen 136 passed；双仓 commit 干净。

## ② Q4 分层有界检索落地（✅ 完成 2026-09-08）

- [x] a. 13 域 `domain` 标签铺全：domain_systems.json 每个 system 增 `domain` 字段（单一真源），
      vector 打标改由 JSON 派生（system/subsystem/device→域）；故障 66/66、报文 22/22、
      信号 116/116、场景 59/59、功能 11/11 全部分区；空域仅剩 10 个"基础设施类 SR"（无功能覆盖，属全局层）
- [x] b. 检索链路：词表路由（13 域）优先 → **图谱定位域**兜底（belongs_to 系统边 + 词元重合闸门防噪声）
      → 域内 topk（13 分区非单池）→ 域内不足/无域信号回退全局（mixed 标注）
- [x] c. 量化指标：`route_source`（terms/graph/''）+ `route_precision`（分区命中率，域查询 =1.0）+ 测试
- [x] d. 覆盖率断言：`test_asset_docs_all_partitioned`（资产类零空域；需求空域==基础设施集）
      + hvac 域路由精度 + 图谱路由单元测试（含噪声不误路由）

**验收**：域查询 bounded 且命中率 1.0；无域查询回退全局（bounded=False）；全量 122 passed / ruff clean。

## ③ Q2-P-A 第二增量扩库（向"数百"推进） — ⏳ Wave A 完成（2026-09-08）

> **进度**：Wave A ✅ + **Wave B ✅ + Wave C ✅（FMEA 202 / 场景 103，达 200+/100+ 门槛，双侧提交 wave-c，全绿）**；
> ③-a/b 已达成（200+/100+）；接下来做 c（多网段）与 d（四向追溯）。

- [ ] a. FMEA 66 → 200+（13 域 × 每域 10-30，真实结构 + 示意实例；继续 12 字段全合法）
      —— ✅ 已 66 → **202**（Wave A+B+C：13 域 × 10-30 覆盖成型，域内多故障编排）
- [ ] b. 场景 59 → 100+（8-15 剧本族 × 变异；无孤儿故障不变量自动扩展覆盖）
      —— ✅ 已 59 → **103**（Wave A+B+C：+44 文件）
- [x] c. DBC 多网段化（✅ 2026-09-08）：DBC GenMsgSegment 单一真源标注 3 网段（vehicle 13/comfort 7/backbone 1）；schedulability 网段级分析（vehicle 9.4%/comfort 1.1%/backbone 0.1% 全可调度）；平台 loader 暴露 MessageDef.segment —— 单总线 10% 上限叙事解除，comfort/backbone 留出扩帧余量
- [x] d. RTM 四向追溯视图（✅）：上游 scripts/gen_trace_4way.py + docs/rtm_4way.md —— SR(52)↔场景/模块↔测试↔故障 机器生成，改 rtm/场景/故障后重跑防漂移
- [x] e. 全链数字终扫（✅）：上游覆盖率口径 98%（2651/55）；文档计数/版本收口 ①-③（上游 v1.12.0 / 平台 v0.4.0）；陈旧数字残留清零
      —— Wave A+B+C 已同步（基础图谱 517 / 向量 506 / 122 passed）

**验收 ✅**：上游 958 collected 全绿；平台 123 passed；无孤儿故障；多网段可调度（vehicle 9.4%/comfort 1.1%/backbone 0.1%）；KB 基础图谱 517/向量 506。

## ④ Q3 原子组合器（"真·原子化组合"）

- [x] a. 组合规划模块（✅）：gent/composer.py plan_compose —— 规则提取点名故障（≤6，MIN_SCORE 3.0，绝不发明）→ 结构化（故障×节点×时序×期望）
- [x] b. 组合→执行（✅）：steps 兼容 /api/run/custom，POST /api/agent/composer{run:true} 走真实引擎（测试端到端 all_passed，202 原子故障 × 库内相似模板）
- [x] c. 三栏溯源（✅）：build_plan provenance 每条故障携带 source_asset（字典字段）/ source_system（13 域标签）/ source_agent（规划说明）+ example_scenarios
- [x] d. 多轮 + 上下文（✅ 组合器/service 层）：composer history 续编（指代式'刚才那个也加上'经前文点名解析，134 passed）；LLM 语义消歧沿用 freeform 候选仲裁，历史经服务端对话层携带

**验收**：一句"门故+超速级联"类意图 → 生成 → 执行全绿 → 逐条可溯源。

## ⑤ 动画原子化 + 产品化

- [x] a. 域特征演示档（✅ 部分）：_domain_alarm 兜底 —— 203/203 故障均可展示结构化告警（名·子系统域/层·期望处置，诚实标注示意）；curated 档案优先级不变（通道级效果仍需按域逐条补，见 b）
- [ ] b. 动画原子库（部分）：已加 8 个 Wave A/B/C 域代表故障的通道级 curated 档案（door_open_moving→EB 等，语义保守映射）；其余 190+ 故障由域特征演示文案兜底 —— 全 13 域逐个通道扩谱待续
- [ ] c. fresume 式产品化打磨（迭代清单）—— 见 docs/ACCEPTANCE_Q2Q4.md「遗留范围」；自动层数据基础已具备
- [ ] d. 浏览器 e2e（迭代清单）—— 需前端构建 + Playwright；API 级等价冒烟已覆盖

**验收**：新域故障动画不再"通用演示"，演示页可展示每域特征化效果。

---
进度：① ✅ · ② ✅ · ③ ✅ 全部 · ④ a-d ✅（组合器+多轮）· ⑤-a ✅ + ⑤-b 部分（curated 示例档案）· ⑤-c/d（产品化/UI/e2e）列为后续迭代清单 —— 本文件随完成情况勾选。

# Iteration A/B/C 症状多跳诊断（v0.5.0，2026-09）— ✅ 完成

> 承接 `docs/HANDOFF_SYMPTOM_DIAGNOSTIC.md`；目标：让 Agent 对「无故障码的症状类描述」
> 做图谱多跳诊断推理（例：仪表盘闪烁无码 → 怀疑供电不稳 → 检查 24V/辅变/电容纹波），
> 而非空响应或错误域的自信答案。

- [x] a. 症状资产层：`domain/data/symptoms.yaml` **12 症状**（hints 全锚 202 真实故障键/13 域；annotation real 7/mixed 5；无孤儿校验 `validate_symptom_assets`）
- [x] b. 因果边 + 多跳遍历：`domain/data/causal_edges.yaml` **54 条**（indicates 41/causes 13；real 41/derived 13 逐条标注）；`knowledge/graph.causal_chain` ≤3 跳（症状→indicates→嫌疑→causes 反查根因），逐跳 basis/note；enrich 后服务态 651 节点/1167 边/640 文档
- [x] c. RAG 多跳诊断规划器：`agent/diagnoser.py` + `POST /api/agent/diagnose` —— 检索症状资产 → 候选链 → 诊断步骤建议（置信度/验证动作/场景复现/溯源）；无命中/证据不足 → “不确定/需补充 X”，derived 显式标注，绝不编造故障码
- [x] d. 专项回归：仪表盘闪烁但无故障码 —— 函数级 + HTTP 级（非空/可溯源/不编造码/含 aux+network 域候选）

**验收 ✅**：上游 957 passed + 1 skip（未改）；平台 **164 passed** / ruff clean；ai-testgen 136 passed（未涉）；计数同步进 README/CHANGELOG/ACCEPTANCE/PROJECT_ARC（见 ACCEPTANCE_Q2Q4.md 文末 Iteration 台账）。
