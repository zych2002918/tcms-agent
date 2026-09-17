# TCMS 平台补强工单（拷问视角审计产物）

> 生成背景：以"反镀金拷问"视角（术语降维 / 信息源审计 / 规模退化 / 状态记忆 / 诚实失败 / 目的真实性）审视 tcms-ai-platform。
> 用法：每条都是"会被最尖锐的人问穿"的点 → 现状（代码事实）→ 补强方向 → 验收门禁。
> **执行状态（2026-09-09）：P0-1/P0-2/P1-1/P1-2/P1-3/P1-4(新增)/P2-1/P2-2/P2-3/P2-4/P2-5 全部完成并勾选 → 工单 11/11。**

## 执行记录（2026-09-09）
- 批次 A（P2-2/P1-2/P1-1）：pytest 184 passed + 1 skipped；ruff clean；web tsc+vitest 绿。
- 批次 B（P2-1/P1-4）：pytest 189 passed + 1 skipped；ruff clean。
- 批次 C（P2-3/P2-4/P2-5）：pytest 192 passed + 1 skipped；ruff clean；压测结果见 P2-4。
- 附产物：`docs/P0-1_TERMINOLOGY_AUDIT.md`、`docs/CONFIDENCE_CONVENTION.md`、`scripts/bench_retrieval.py`、`.github/workflows/ci.yml`。
- 纪律：未 git commit（等指令）。

## 今晚已核实的代码事实（不用重复查）

- `agent/diagnoser.py:142` `diagnose_symptom` = 固定管线（症状检索→因果链≤3跳→确定性置信度→可选 LLM 候选内仲裁）。LLM 只对前 4 候选重排，返回键过"有效集过滤"（:362-367），失败回退规则。
- `agent/llm_backend.py` = OpenAI 兼容 **chat 补全，无 function-calling**；`MockAgentBackend` 兜底离线。
- `knowledge/vector.py:57-69` 默认 `HashedEmbedder`（md5 字符哈希 256 维）——**非真语义模型**；`Embedder` 抽象已留插口。
- `knowledge/retriever.py`：词表路由→`_route_via_graph`(≥3 字符重合闸门)→域内有界 top-k→不足 `mixed=true` 回退全库；BM25+RRF 融合；命中带图谱邻接证据。
- `knowledge/graph.py:217` `causal_chain` 只走 `indicates/causes`；`:276` `shortest_path` BFS 逐边带 basis/note。
- 记忆：`/api/agent/diagnose` **无 history**（`server/app.py:1062`）；仅 compose/advisor 收 `history` 且只做"点名续编"（`composer.py:86-116`）。
- `GraphSink.record_run` 已接线 4 处（`app.py:937/1171/1264/1295`），run 节点进图，**当前不参与召回/查询**。
- 13 域单一真源 `domain/data/domain_systems.json`（`vector.py:178`）。
- 平台**无 `.github/workflows`（零 CI）**；上游 tcms-can-test 有 CI 先例可抄。
- 量级：644 检索文档 / ~655 图节点 / 检索 O(N) 线性扫；接口已抽象，10^5+ 才需 ANN。

---

## P0 —— 一句话就能被问穿，先修

### ✅ P0-1 [术语审计] "agent / 语义检索"措辞与代码能力对齐（2026-09-09 完成）
- 拷问：你说用了 agent，它自主调了什么工具？你说语义检索，embedding 是什么模型？——现在答案会自相矛盾。
- 现状：文档/README 口径宽于实现（agent=受约束决策管线；检索=字符级哈希+图证据，非语义理解）。
- 方向：全仓审计 `Agent/智能体/语义/RAG` 表述，逐处对齐到代码事实；保留"GraphRAG 风格混合检索(字符级确定性通道 + 图谱显式边)"这类准确措辞。
- 验收：审出清单 + 修订 diff；改完跑 `pytest -q`（文档字符串在 doctest 外不影响，但防串）。
- 量级：小。价值：先不镀金，后续真能力上线再放开措辞。
- **结果**：审计清单 `docs/P0-1_TERMINOLOGY_AUDIT.md`（修订 16 处 + 保留项分类）。修订：README 语义检索/语义近义/6 维语义评审→KB 锚定规则评审等；web UI「真实语义评审」→「KB 锚定评审」、领域语义→领域知识等；app.py 5 处「RAG 语义澄清/advisor 语义理解」→「KB 检索澄清/意图识别」；检索栈模块 docstring 措辞对齐（vector/lexical/retriever）。保留项：graph.py「语义层」等分类学义项、freeform 内部注释、视觉/交互语义、历史 docs 记录。pytest/ruff 通过。

### ✅ P0-2 [真语义向量通道] 实现真 embedding，近义召回才有资格说"语义"（2026-09-09 完成，路线=API + 自动降级）
- 拷问：数据扩到 10 万条、用户问"灯闪"，你靠字符哈希怎么知道它≈照明异常？
- 现状：`HashedEmbedder` 无近义能力；`Embedder` 抽象插口已存在，无真实现。
- 方向：新增真 embedding 实现（本地 BGE 或 API embedding），无 key/离线自动落回 HashedEmbedder（复用 llm_backend 的诚实降级模式）；配 14 条既有 golden 防回退 + **新增近义改写 golden**（如「灯闪/频闪」「受电弓/弓网」）证明增益；词表域路由对口语别名失效时走图定位/真语义兜底。
- 验收：新近义 golden 命中率 > 旧通道；既有 14 条无回退；无 key 环境全绿（降级路径有测试）。
- 量级：中。价值：L2 信息源审计层的核心软肋。
- **结果**：
  - `knowledge/vector.py`：`ApiEmbedder`（OpenAI 兼容 `/embeddings`，模型解析=显式→env EMBEDDING_MODEL→GET /models 自动探测；失败逐条降级；`api_active` 供上层判断真通道是否激活）+ `Embedder.embed_batch` 基座；`VectorStore.add_many` 走批量嵌入（哈希行为等价、分区上限语义不变）。
  - `agent/llm_backend.py`：`make_kb_embedder()` 工厂（base_url/key 与对话 LLM 同源解析；`TCMS_EMBEDDER=api` 显式开启，否则默认哈希零网络——启动/离线行为与旧版一致）。
  - `server/app.py` store 构建接线（默认哈希；开 API 通道才真嵌入）。
  - golden：14 条既有检索 golden 无回退（混合 14/14、纯向量 ≥13/14 实测仍过）；新增近义改写 golden（`tests/test_embedding_semantic.py`，5 对 TCMS 口语/故障改写，条件执行：仅 `TCMS_EMBEDDER=api`+真通道可用时断言单对胜出 ≥80% + 平均余弦边际 >0.05，否则 skip，绝不把哈希余弦当语义证据）；离线单测 `tests/test_api_embedder.py`（假 transport 7 例：真调 /embeddings、批量单请求、HTTP 错误/无 key/探测失败降级哈希、自动探测模型、add_many 批量路径与分区上限）。
  - README 配置表新增 `TCMS_EMBEDDER` / `EMBEDDING_MODEL` 行与措辞。
  - pytest/ruff 通过（无 key 环境全绿：降级路径有测试）。

---

## P1 —— 能力实质提升

### ✅ P1-1 [多轮诊断记忆] `/agent/diagnose` 加 session 级锚点记忆（2026-09-09 完成）
- 拷问：窗口内连续追问，你记不记得上一轮说的部位？不记得，agent 是不是空名？
- 现状：diagnose 单轮无状态，唯一 history 是 compose 的"点名续编"。
- 方向：加可选 `session_id`；服务端存**锚点式记忆**——只存证据引用（`fault:x`/边/前轮候选 + 用户补充的现象事实），不存散文摘要（防"压缩丢语义"，语义本体留在图上）；追问轮可引用前轮候选继续走链。
- 验收：新增 3~5 条多轮 golden（如"再说一下 + 刚才那个部位"续诊）；会话过期清理有测试。
- 量级：中。
- **结果**：`agent/diagnose_memory.py`（AnchorMemory：TTL 过期清理 + 事实队列去重封顶 + 线程锁 + 会话数上限；build_anchor 只存 symptom 引用/真实候选键/用户事实）；`diagnose_symptom(session_anchor=…)`——直配落空且带指代词（刚才/继续/那个部位…）→ 复用上轮症状锚点走链，reply/evidence.session 如实标注 anchor_used（证据引用非摘要）；HTTP 端点读写 + 空消息不覆盖好锚点。多轮 golden：续诊沿用锚点、换题不误用、无锚点追问诚实 no_match、TTL/事实合并单测 + HTTP 级会话黄金路径（tests/test_diagnose_sessions.py、test_platform HTTP）。

### ✅ P1-2 [主动澄清] 候选不可区分时追问"最小观测集"（2026-09-09 完成）
- 拷问：top1/top2 置信度只差 0.01，你敢拍板吗？好的诊断系统该说"缺哪个观测能区分 A 与 B"。
- 现状：一次给完候选 + 二元 no_match/uncertain，无信息增益式追问。
- 方向：候选置信度接近（如差距 <0.15 或 top 与次 top 同域同 hop）→ 输出需补充的区分性观测（哪个信号/哪个工况），把 no-match 升级为"guided clarification"。
- 验收：新对抗 golden——模型在可分性不足时输出追问而非硬排；仍不发明。
- 量级：小-中。
- **结果**：`diagnoser._ambiguity`（top1/top2 差 <0.15 且同域或同跳 → clarification{between, distinguishing_observations(全部取自真实 check/detect/场景), hint}）+ reply 追加"不硬排第一 + 需补充区分性观测"段落；响应顶层带 `clarification`（无歧义=None）。测试：纯函数门禁四态 + 真实"仪表盘闪烁"(同域 0.85 并列 → 追问 aux_24v vs aux_converter) + 领先样本"制动灯常亮"(real .85 vs derived .5 → 不追问)。不发明不变：候选仍全部真实故障键。

### ✅ P1-3 [失败样本对抗集] 诚实与失败的第二道门禁（2026-09-09 完成）
- 拷问：什么输入会让它答错？系统怎么知道自己错了？——要有"负样本"才答得上来。
- 现状：golden 全为正向（检索 14 / 诊断 8）；no_match 路径无回归负样本锁。
- 方向：固化对抗集：同域多候选易混句 / 跨域近义句 / 含"故障/报警"干扰词的无码症状 / 空白与胡话——门禁=**不得从 no_match 变发明、不得错域、不得输出候选外故障键**。
- 验收：对抗集全过 + 计入 `pytest`；新增条目需先证明"当前版本会错"再修（防橡皮图章）。
- 量级：中。价值：把"诚实纪律"从代码注释变成机器门禁。
- **结果**：
  - 数据 `domain/data/diagnose_adversarial.yaml`（26 条，A/B/C/D 四类；expect_no_match 17 条 + 命中指定症状 9 条；[P1-3-fix] 标注 3 条先证旧实现会错再纳入）。
  - 门禁 `agent/evals.py::evaluate_adversarial`（机器校验三不变量：不编造 / no_match 诚实引导 / 命中指定症状且候选域 ⊆ 症状声明域）。
  - 测试 `tests/test_diagnose_adversarial.py`（全过 + 结构自检：四类齐备 + 修复条目存在）。
  - **顺带修复真 bug**（对抗探针实测发现）：`match_symptom` 共享字闸门原先比对**整篇向量文档**（含"疑似候选故障：列车级时间同步丢失"等模板尾巴），任何含"列车"的无码废话（如「列车地板漏水了」「这列车整体都正常吧」）都会误中 network 症状。修复：闸门只对症状**本体**（name+description）计数，并剔除车辆框架/状态类弱证据字（列车/车厢/状态/正常/吗…），召回面与既有 8 诊断 golden 不变。
  - pytest/ruff 通过。

---

## P2 —— 规模 / 工程 / 交付

### ✅ P2-1 [出处链] 知识资产附权威出处，证据可机器追溯到底（2026-09-09 完成）
- 拷问：你的 54 条因果边凭什么？real_mechanism 依据哪份资产/哪条规则？——现在答不出文件级出处。
- 现状：边有 basis/note 但 note 是自由文本，无结构化的"依据资产文件:条目"链接。
- 方向：fault/symptom/real_mechanism 边增 `source` 字段（引擎资产 `文件:key` 或派生规则 id）；`evidence` 输出带 source；抽查测试 54 边+12 症状全有出处。
- 验收：`/api/agent/diagnose` 的 evidence 每链可点到资产文件级。
- 量级：中。
- **结果**：`KnowledgeGraph.node_asset_ref(node_id)` 统一出处解析（单一冒号 `file:key`；fault→faults.yaml、symptom→symptoms.yaml、system→domain_systems.json、message/signal→tcms.dbc、scenario→scenarios/<file>、req→rtm、function→engine-functions 等，未知 kind 原样返回不伪造）；诊断每条候选 chain 带 `from/to/refs`、evidence 顶层带 `edge_refs`（全部出处并集）→ `/api/agent/diagnose` 每链可点到资产。测试 tests/test_provenance_weaklinks.py：54 边+12 症状出处可解析且 key 全真实、诊断 evidence/HTTP 链 refs 齐全。

## P1-4（2026-09-09 新增）[弱证据升级] 无直接边时的可溯源关联（资产锚点 + 2 跳路径）
- 拷问（用户提出）：A、B 之间没有直接边时，除了加"语义/从属集"，有没有更优解？——把 1 跳邻接证据升级为"结构可溯源的弱关联"。
- 方向：检索命中除 1 跳邻接外，携带 ① `source_ref`（命中资产出处）② `anchor_stats`（邻接资产按类计数：场景/信号/需求/功能…＝资产锚点统计）③ `weak_links`（经一个中间节点的 2 跳可达样例，≤4 条）——A/B 无直接边时仍给出"经谁可达"的路径证据，路径节点真实存在于图（不发明）、每条带资产出处。
- 验收：检索响应命中带三类字段；weak_links 节点全部在图中且 ref 可解析；邻接出处机器可追溯。
- **结果**：retriever._pack 富化 + `_two_hop_samples`（3×3 有界采样防爆量）；测试 5 条：出处正则/真实存在、邻接 ref、weak_links 全真实节点（结构稀疏时 skip 非失败）。门禁保持：不发明（节点必须在图）、不错域（路由逻辑未变）。

### ✅ P2-2 [置信度口径] `_CONF` 拍脑袋表 → 可解释口径 + 校准文档（2026-09-09 完成）
- 拷问：0.85 是什么概率？凭什么 1 跳 real 就是 0.85？——答不出就是伪精确。
- 现状：`diagnoser.py:35` `_CONF` 手工定值，无数理/经验依据说明。
- 方向：定义"置信度=依据充分性的排序分数，非概率"并在 schema/文档写明；或与真实执行复现结果做校准分析；加口径锁测试防未来漂移。
- 验收：文档化语义 + 测试锁值；UI 提示语不暗示概率。
- 量级：小。
- **结果**：口径文档 `docs/CONFIDENCE_CONVENTION.md`（排序分非概率、取值表、单调性、为何不是概率、未来校准须另出字段）；`_CONF` 注释/模块 docstring/API docstring 同步；UI 去掉"置信度 X%"，改为"依据分/排序分"+非概率说明（AgentPage 诊断卡与解析卡 + title tooltip）；锁值测试（表全量 + 真实>derived、跳数单调、越界 clamp/回落默认）→ tests/test_diagnose_sessions.py。README 亮点同步为"排序分（依据充分性，非概率）"。

### ✅ P2-3 [L2 运行记忆参与召回] run 节点从"记录"变"证据"（2026-09-09 完成）
- 拷问：你说有组织记忆——执行过 100 次的场景对诊断一点影响没有，那记忆在哪？
- 现状：`record_run` 已接线 4 处，run 节点进图，但不参与召回/查询。
- 方向：候选/证据中标注"该故障近期有真实执行记录(passed/failed)"或使 `/api/kb/path` 可达 run 节点；加"run→证据"链路测试。
- 验收：一次真实 run 后，相关 diagnose/检索 evidence 中可见 run 引用。
- 量级：小-中。
- **结果**：检索 `_runs_index()`（executed 边 → 每命中 recent_runs，最近 3 条新在前，一次调用只建一次）+ 命中行带 `recent_runs`；诊断 `_recent_runs(graph, 候选场景文件)` → evidence.recent_runs（含 run_id/scenario/passed/failed/all_passed/ref=runtime:run_id）。诚实纪律：未跑过=[]。测试 tests/test_run_memory_recall.py（3 例）：无 run 时为空、record_run 后 diagnose evidence 可见其复现场景的 run 引用、检索命中被执行场景文档带 recent_runs。

### ✅ P2-4 [规模退化预备] 检索复杂度与稳定性压测脚本（2026-09-09 完成）
- 拷问：文档×10、×100，top-k 还稳吗？每查询多少 ms？
- 现状：O(N) 线性扫，640 条无碍；无 benchmark 佐证。
- 方向：benchmark 脚本（文档子集×2/×5/×10 的 top-k 重合度与耗时）+ golden 冒烟；结果写回本表。
- 验收：报告 N 增长下 top-k 重合度曲线；若劣化明显再引入 ANN(hnswlib) 抽象实现。
- 量级：小。
- **结果**：`scripts/bench_retrieval.py`（语料按同文本副本 ×1/×2/×5/×10 放大；20 查询 × top5；输出每查询 ms、golden 门禁、相对 ×1 的 top5 重合率；`python scripts/bench_retrieval.py` 可复现）。实测（640 基数）：
  ```
  ×1  640 文档  1.53 ms/查询  golden 14/14  top5 重合 100.0%
  ×2  1280      1.28 ms      golden 14/14       37.0%
  ×5  3200      2.07 ms      golden 12/14       15.7%
  ×10 6400      3.75 ms      golden 12/14       7.4%
  ```
  结论（诚实口径）：同文本副本是"最坏近邻膨胀"——近似组内并列被放大、把期望项挤出 top5（fault:cabin_light_flicker 被 symptom 副本群淹没）；耗时仍近似线性（O(N) 3.75ms@6400），**退化主要在 top-k 稳定性而非速度**。由此预留下一步：近邻池近似去重/阈值化 + 10^5 量级再上 hnswlib（Embedder/VectorStore 接口已抽象，检索代码零改动）。

### ✅ P2-5 [平台 CI] 让"164/16/5"变成 push 门禁（2026-09-09 完成）
- 拷问：你所有门禁数字是本地跑的，谁证明？
- 现状：平台零 `.github/workflows`；上游有 CI 可抄（pytest+ruff，badge bot 会自提交，注意 fetch+ff-only）。
- 方向：平台加 workflow：pytest+ruff → vitest → e2e(起本地 server)；README 挂 badge；提交后若 bot 前移 remote 记得同步。
- 验收：push 后 Actions 全绿。
- 量级：小-中。
- **结果**：`.github/workflows/ci.yml`（ubuntu，双 checkout：platform + 上游 tcms-can-test 到 `$GITHUB_WORKSPACE/tcms-can-test` 使 NEEDS_UPSTREAM 全量资产测试可跑；python3.11 → `pip install -e ".[test,lint]"` → ruff → pytest 全量 → node20+pnpm9 → vitest；push/PR 到 main/master 触发 + 并发取消）。e2e 需起本地 server，成本高未入 CI（本地脚本保留）。README 已挂 Actions badge。注：未 commit → push 门禁在首次提交后生效，Actions 首次运行需在仓库 Settings 开启。

---

## 完工状态（2026-09-09）
- 工单 11/11 全部完成并勾选（P0-1/P0-2/P1-1/P1-2/P1-3/**P1-4 新增**/P2-1~P2-5）。
- 基线：`pytest -q` = 205 passed + 1 skipped（追加 ROI P1-a function-calling 6 例与 P1-b MCP 7 例后；skip=真语义近义条件 golden）；`ruff check src tests` clean；web vitest 16/16；图谱交互 e2e 5/5。
- 追加能力（见 docs/TECH_DEPTH_AUDIT.md §11/§12）：受约束 function-calling（/api/agent/toolassist，
  含 kb_filter_assets 过滤工具）、最小 MCP server（python -m tcms_ai_platform.agent.mcp_server）、
  Agent 对『仅告警/降级仍可运行』类问题的确定性枚举（/agent/free kb_answer）——以问题驱动继续改进，
  不设“新功能冻结”（见 §12 修订）。
- 下一步（问题驱动）：CI 已绿（GitHub Actions, Node22+pnpm11 修复）；后续按被戳穿的缺口逐条修。
- 纪律：未 git commit（等指令）→（2026-09 用户已授权全量推送，HEAD 已在远端）。

## 全部改动清单（2026-09-09，未 commit）
- **docs/**：HARDENING_BACKLOG.md（勾选/记录）· P0-1_TERMINOLOGY_AUDIT.md（审计清单）· CONFIDENCE_CONVENTION.md（置信度口径）
- **.github/workflows/ci.yml**（新增，pytest+ruff+vitest 双仓 CI）
- **scripts/bench_retrieval.py**（新增，检索规模退化压测）
- **README.md**（术语措辞 + 配置表 + 诊断/检索亮点口径 + CI badge）
- **knowledge/**：vector.py（ApiEmbedder/embed_batch/add_many）· graph.py（node_asset_ref 出处）· lexical.py · retriever.py（docstring、P1-4 弱证据 source_ref/anchor_stats/weak_links、P2-3 recent_runs）· __init__.py
- **agent/**：diagnose_memory.py（新增 AnchorMemory）· diagnoser.py（P1-1/P1-2/P2-1/P2-2/P2-3/P1-3 闸门）· llm_backend.py（make_kb_embedder）· evals.py（对抗集评测）
- **server/app.py**（diagnose session 接线 + 措辞）· **domain/data/diagnose_adversarial.yaml**（26 条对抗集）
- **tests/ 新增**：test_api_embedder.py · test_embedding_semantic.py · test_diagnose_adversarial.py · test_diagnose_sessions.py · test_provenance_weaklinks.py · test_run_memory_recall.py · test_toolassist.py · test_mcp_server.py
- **ROI 追加（2026-09，见 TECH_DEPTH_AUDIT）**：agent/toolassist.py（function-calling 受约束工具面）、agent/mcp_server.py（零依赖 MCP stdio server）、server/app.py ToolAssistRequest+端点、agent/llm_backend.py._chat_tools、scripts/verify_semantic_channel.py（语义通道验证）· README/TECH_DEPTH_AUDIT 同步
- **web/**：AgentPage.tsx（多轮会话/澄清/排序分措辞）· GraphWorkspace.tsx · ScenariosPage.tsx · OnboardingModal.tsx · api.ts（diagnose 契约）
