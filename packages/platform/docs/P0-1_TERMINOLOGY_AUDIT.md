# P0-1 术语审计清单（拷问视角：口径对齐代码事实）

> 状态：2026-09-09 完成。#1–#16 修订已落地（README/web UI/app.py/检索栈 docstring）；
> 保留项逐条核实为合法义项或内部机制注释。P0-2 落地后复核 #2/#8/#9/#14/#15 措辞与
> 新真语义通道一致（已随 P0-2 一并更新为"哈希默认/真嵌入可选"的最终口径）。
> 门禁通过：pytest 全绿 + ruff clean。

> 审计基准：HEAD cc174e6（v0.5.0）。判定标准 = 该措辞是否**宽于实现**：
> - 检索层事实：默认 `HashedEmbedder`（字符级 md5 哈希向量，无近义）+ BM25 词法 +
>   图谱显式边证据 → **非语义理解**；真 embedding 通道为 P0-2 新增（API，无 key 自动降级）。
> - Agent 事实：受约束决策管线（检索→真实执行→**规则评审**）；Reviewer 6 维 = 关键词/节点
>   存在性规则检查（非 LLM、非语义）。LLM 仅在配 key 时参与规划/消歧/文案。
> - "语义"的其他合法义项（分类学、数据模型描述、动画视觉语义、图遍历方向解释）不在审计范围。

## 修订项（口径宽于实现 → 已改/将改）

| # | 位置 | 现状措辞 | 代码事实 | 判定 | 修订 |
|---|---|---|---|---|---|
| 1 | README L71 | 「知识图谱工作台」：**语义检索** + 关系图谱 | 检索=字符哈希+BM25+图谱证据 | 改 | 混合检索（词法 + 图谱证据，离线确定性通道）|
| 2 | README L77 | 向量+BM25 双通道「兼顾**语义近义**与字面精确」 | 哈希通道无近义 | 改 | 双通道=向量（默认字符哈希/可选真语义嵌入）+BM25，重字面/近形精确 |
| 3 | README L93 | **6 维语义评审** | Reviewer=6 维 KB 锚定**规则**检查 | 改 | 6 维 KB 锚定规则评审 |
| 4 | README L94 | Agent **理解**后查证 | free 解析=规则+可选 LLM 消歧 | 改 | 解析意图后查证 |
| 5 | README L140 | knowledge/…+ GraphRAG + 沉淀 | GraphRAG **风格**混合检索（字符级确定性通道+图谱边） | 改 | GraphRAG 风格混合检索 |
| 6 | web AgentPage L820 | 面板标题「真实**语义**评审」/L816 注释「真实领域语义」 | 规则评审（KB 锚定） | 改 | 「KB 锚定评审」/注释同义 |
| 7 | web AgentPage L74 | 评审维度 `domain_aware` 标签「领域**语义**」 | 维度=联锁/机制关键词命中率 | 改 | 「领域知识」 |
| 8 | web GraphWorkspace L229 | 混合检索（**语义** + 图谱邻接） | 见 #2 | 改 | 向量/词法 + 图谱邻接 |
| 9 | web GraphWorkspace L281 | 注释「域内**语义** topk」 | 域内向量 topk（哈希/或真嵌入） | 改 | 域内向量 topk（P0-2 落地后真嵌入可称语义）|
| 10 | web ScenariosPage L1055 | 顾问思考中：**语义检索**故障知识 | advisor 检索=同一混合通道 | 改 | 检索故障知识 |
| 11 | web OnboardingModal L215/L382 | LLM 负责「…/**语义理解**」 | LLM 实际做规划/选场景/意图解析 | 改 | 「意图解析」 |
| 12 | server/app.py L216/L1021/L1034/L1202 | **RAG 语义澄清**（“你可能指这些”） | 规则零候选 → KB 检索候选澄清 | 改 | KB 检索澄清 |
| 13 | server/app.py L1092/L1097 | advisor **语义理解** / 语义不明时 | advisor=规则识别+可选 LLM 消歧 | 改 | 意图识别 / 意图不明时 |
| 14 | knowledge/lexical.py docstring | 「向量是**语义通道**…保住'**语义近义**'」 | 哈希向量无近义（docstring 自相矛盾地预留真 BGE） | 改 | 按 P0-2 落地后的真实架构重写（哈希默认 / 真嵌入可选）|
| 15 | knowledge/retriever.py docstring | 「域内**语义** topk」 | 域内向量 topk | 改 | 同 #14 一并重写 |
| 16 | agent/reviewer.py docstring | 「**多视角真实规则评审**…不是 LLM 自评」 | 表述已准确 | 留 | ——（已准确）|

## 检查后保留项（合法义项 / 内部机制注释，不改）

| 位置 | 措辞 | 为何保留 |
|---|---|---|
| graph.py L33 / L193 | 「Q2 语义层」「边带语义（方向即解释方向）」 | 分类学/图遍历语义，不同义项 |
| freeform.py L136/L276/L282 | bigram「语义近似命中」/ LLM prompt「语义解析器」 | 描述规则近似 / LLM 真实解析（配 key 时成立）|
| advisor.py L100-101/L377-381 等 | 「具体 TCMS 语义」「含门语义（子串噪声）」 | 内部机制注释，且已自陈子串误判缺陷 |
| tasks.py L45 / core/models.py L144 / enrichment.py L361 | 「禁止发车语义正确」「模式语义」「标题语义」 | 领域数据/描述语义，非检索语义 |
| web GraphWorkspace L500 / FaultLabPage / ui.tsx / graph3d | 「语义色 / 动画语义 / 缩放语义」 | 视觉/交互语义，非 AI 能力声明 |
| web AgentPage L168 | 「LLM 语义理解」（resolver=llm 分支） | 条件成立（真 LLM 消歧时）|
| README L6/L49 等 | Agent 自证 / 离线 Mock | 与实现一致（受约束管线+真实执行+规则自证）|
| docs/*.md 历史记录（ACCEPTANCE/ROADMAP/SESSION_RECAP/NEXT_PHASE 等） | 语义通道等计划性措辞 | 开发历程快照，非当前能力声明 |

## 门禁
- 修订后 `python -m pytest -q` 全绿（164）＋ `ruff check src tests` clean。
- P0-2 落地后再复核 #2/#8/#9/#14/#15 是否需随真嵌入通道调整措辞。
