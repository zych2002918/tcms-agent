# 交接：新对话启动包 —— 症状/无码故障多跳诊断能力（Iteration A→B→C）

> **状态：Iteration A/B/C 已在 v0.5.0 完成（工作树，未提交）** —— 本启动包任务已执行完毕，
> 验收台账见 `docs/ACCEPTANCE_Q2Q4.md` 文末「Iteration A/B/C 症状多跳诊断验收台账」。
> 若后续会话要基于本能力继续迭代，请先读那份台账（含遗留清单），勿把下述 A/B/C 当作未做任务。

---

> 本文档 = 资产盘点 + 新对话完整启动 Prompt。直接把它粘给新对话即可开工。

---

## 一、新对话启动 Prompt（可直接粘贴）

```
你在维护本地一个"TCMS 列车测试 + AI Agent"平台（Windows；两仓库在 E:\DSHworkplace\objects\ 下：
上游引擎 tcms-can-test 与平台 tcms-ai-platform 为兄弟目录）。上一轮已完成 Q2-Q4 路线图
（验收台账见 tcms-ai-platform/docs/ACCEPTANCE_Q2Q4.md，含遗留清单）。现在要做新能力迭代：

【目标】让 Agent 对"无故障码的症状类描述"能做图谱多跳诊断推理（例：仪表盘闪烁但无故障码 →
怀疑供电不稳 → 建议检查 24V/辅变输出/电容纹波），而不是空响应或给错误域的自信答案。
分三步落地，每步双侧测试全绿、数字/文档机器自证、诚实纪律（不编造，示意标注 derived）。

A. 症状/无码故障资产层（上游数据 + 平台知识层）
   - 在上游 tcms-can-test 故障字典（tcms/faults.yaml，现 203 条）之外建立"症状资产"：
     建议新增独立文件 tcms/symptoms.yaml（或平台侧 assets），schema 至少含
     key/name/中文症状名/涉及域(13 域)/可能的故障键 hints/证据或来源/示意标注；
     首批 ≥12 个真实症状：仪表盘(显示屏)闪烁、HMI 无显示、灯具闪烁、客室灯组频闪、
     大屏花屏、时钟跳变、网络时断时续、SOC 跳变、速度瞬时归零、开门到位灯闪、报警音误响、
     制动灯异常等——每条症状标注"最相关域 + 2-4 个候选故障键 + 推理依据(真实工程因果或 derived 标注)"。
   - 不可与现有故障键冲突；命名语义化；无孤儿/语法校验沿用仓库纪律（可加 symptoms 校验测试）。
B. 因果边与多跳遍历（平台知识图谱 graph/enrichment）
   - 在 knowledge/enrichment 增加"因果边"注入：symptom -indicates-> fault/suspect-system；
     以及 fault -causes-> fault 的工程因果（如 24V 欠压/辅变输出异常 → HMI/显示闪烁；
     电源模块纹波 → 显示闪烁；SOC 估算跳变 ← 电流采样漂移 等，仅在真实因果或带 derived 标注时建边）。
   - 单源 = 一张可审计的因果表（仿 domain/data/domain_systems.json 或 enrichment JSON 模式，
     注明 each 边的依据：真实机制/示意 derived）。
   - 新增图谱遍历能力：从症状节点出发 depth=2~3 返回 candidate 故障链（复用 graph.subgraph 或加
     专用 BFS 过滤 causes/indicates 边），并支持"每跳给出边依据"。
C. harness/RAG 多跳诊断规划器（agent 层）
   - 新端点/函数：输入症状文本 → 1) kb 检索症状资产(RAG) → 2) 图谱沿因果边取候选链 →
     3) 生成"诊断步骤建议"（验证哪条故障/哪个报文/哪个检查动作）→ 4) 结果带
     置信度与 evidence(每条建议溯源)；无足够证据时必须输出明确"不确定/需要补充 X"，
     严禁编造故障码。
   - 噪声规避：复用并强化现有检索纪律（见下"红线"）；当症状文本无任何词汇/因果命中时给
     honest 空 + 引导，绝不硬答。
   - 每步测试：症状资产校验、因果边数量/方向抽查、诊断规划端到端（fake symptom→建议含
     目标故障且溯源），以及"仪表盘闪烁但无故障码"专项回归（必须：不空答且不编造故障码、
     建议中含 供电/显示相关域 候选）。

【红线（沿用全仓库）】
1. 数字机器自证：计数/关系全部派生自真实文件；改动任何计数后同步以下测试与文档：
   平台 tests/test_knowledge.py、test_platform.py、test_bundled_snapshot.py（精确计数断言），
   以及 README/CHANGELOG/PROJECT_ARC/ROADMAP/ACCEPTANCE 里的数量表述。
2. 不编造车型数据：真实结构 + 示意实例要标注 derived；symptom/因果边每条注明依据类型。
3. 域词汇单一真源 = domain/data/domain_systems.json 的 domain 字段 + subsystems/device_system；
   新增 subsystem/设备必须同步该 JSON、loader 的 _SUB_TO_SYS/设备表与 13 域标签。
4. DBC 段归属单一真源 = tcms.dbc 的 GenMsgSegment（vehicle/comfort/backbone）。
5. 无孤儿：字典/资产新增键必须被某资产消费（故障→场景、症状→图谱候选），否则测试断言失败。
6. 诚实输出：无命中 → no_match/澄清/不确定；LLM 只做候选内仲裁，不许自由发明故障。
7. 未经用户明确要求不要 git commit；改动保留在工作树，交付时列清单。
8. 参考既有自动化纪律：期望动作=字典 action；SIL≥3 需 detect/inject 非空且足够长。

【工具/运行】
- 上游测试：E:\DSHworkplace\objects\tcms-can-test\.venv\Scripts\python.exe -m pytest tests -q
- 平台测试：E:\DSHworkplace\objects\tcms-ai-platform\.venv\Scripts\python.exe -m pytest tests -q
- ruff 门禁：两个 venv 的 -m ruff check（上游 tcms tests examples；平台 src tests ai-testgen）
- 本地服务：127.0.0.1:8000（后端 python -m tcms_ai_platform.server.app，前端 web/dist 由
  FastAPI 静态托管；前端改动需在 web/ 下 node node_modules/typescript/bin/tsc -b 再
  node node_modules/vite/bin/vite.js build，随后重启后端进程或告知用户强刷页面）。
- 读中文文件用 read 工具（Windows PowerShell 终端读 UTF-8 会乱码，勿据此改文件）。

【完成后验收】
- 上游/平台/（如涉 ai-testgen 则复测 E:\DSHworkplace\objects\tcms-ai-platform\ai-testgen\.venv）
  全绿；症状资产与因果边有测试覆盖；"仪表盘闪烁但无故障码"诊断回归通过（非空、可溯源、
  不编造故障码）；新增数量/口径同步进 README/CHANGELOG/ACCEPTANCE 并汇报改动文件清单。
```

---

## 二、资产盘点交接（给新对话的上下文）

### 版本与量级（实测，2026-09-08）
- 上游 tcms-can-test **v1.12.0**：958 collected（957 passed+1 skip）、ruff clean；DBC 22 帧/116 信号/11 节点、`GenMsgSegment` 多网段（vehicle13/comfort7/backbone1）、FMEA **202** 条、场景 **103**、SR-01~52、RTM 四向追溯 `docs/rtm_4way.md`（生成器 `scripts/gen_trace_4way.py`）、覆盖率 98%（2651/55）。
- 平台 tcms-ai-platform **v0.4.0**：134 passed + ruff clean；基础图谱 517 节点/743 边/向量 506 文档（服务态含 enrich：~459 节点 738 边 448 文档口径请以实测为准，计数以测试断言为锁定值）；13 系统域分类法（domain_systems.json 每 system 带 `domain`）；功能 11；KB 分区路由（route_source/route_precision）。
- ai-testgen（平台内子包，独立 venv `tcms-ai-platform/ai-testgen/.venv`）：136 passed/28 skipped。
- 服务运行于 http://127.0.0.1:8000（当前会话以后台进程拉起；新会话如不在，用 platform venv `python -m tcms_ai_platform.server.app` 启动）。

### 当前未提交（15 个平台文件，勿丢失；如需先提交请询问用户）
- src：`agent/freeform.py`（新增 `llm_context` 参数注入 LLM 消歧提示）、`knowledge/retriever.py`（图谱路由加"通用字排除"，防无码症状被"故障/处置"等词带进错误域）
- tests：`test_freeform.py`（llm_context 两测 + 红线不绕过）
- web：7 页 UI 外壳改居中流体（pages/*.tsx），前端产物 web/dist/*（含新哈希 index-CeNeOJBL.js/css 与旧文件删除）
- 上游仓库工作树干净。

### 关键文件索引
- 平台知识层：`src/tcms_ai_platform/knowledge/{graph,vector,retriever}.py`（域单源在 vector `_domain_table()`，读取 domain/data/domain_systems.json）；`domain/{enrichment.py, data/domain_*.json}`（领域知识注入，inject_systems 在 enrichment.py）。
- Agent：`agent/{freeform,advisor,composer,tasks,harness,reviewer,llm_backend}.py`；端点：/api/agent/{free,advisor,compose,composer,run}、/api/kb/{search,subgraph,node,nodes}、/api/run/*、/api/faultlab/*。
- 上游引擎：`tcms/{faultdb,faultlevel,faultlife,scenarios,schedulability,protocol,simulator,network,watchdogs,ebm,atp,...}.py`；数据 `tcms/tcms.dbc`,`tcms/faults.yaml`,`scenarios/*.yaml`,`tests/rtm.csv`。
- 路线图/台账：平台 `docs/{ROADMAP_Q2Q4,ACCEPTANCE_Q2Q4,NEXT_PHASE_BLUEPRINT,PROJECT_ARC}.md`。

### 已实证的问题与本轮要补的缺口
- `/api/agent/free` 对"仪表盘闪烁但无故障码"：no_match=true（诚实，不编）✓
- `/api/agent/advisor`：custom_proposal+需澄清，方向建议合理但需确认是否 KB 接地（新会话可在 C 步做 grounding 检查）
- `/api/kb/search` 修复前：图谱路由误入 traction 域给错自信答案；修复（通用字排除）后：bounded=False 回退全局 ✓（knowledge 18 项测试通过）
- 图谱无 symptom 实体、无 fault→fault 因果边、星型结构 → 本次 A/B/C 就是要补这条链路，并以"仪表盘闪烁但无故障码"为回归验收。
```
