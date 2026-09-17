# 技术参与深度审计（Agent 岗位胜任力自白）

> 目的：以"Agent 相关岗位面试官会逐词拷问"的清单（长对话/上下文/记忆/技能/MCP/
> harness/RAG/图谱/语义检索/评测）逐项对照 tcms-ai-platform **代码事实**，回答：
> "这些技术到底有没有参与？参与多深？证据在哪？缺口是什么？诚实口径是什么？"
> 依据：HEAD cc174e6 + 2026-09 全量改进（HARDENING 11/11），pytest 192+1 / ruff clean /
> vitest 16 / e2e 5 断言；全部未 commit（等指令）。
> 纪律：**只写代码里能指到的地方**；没有就是没有，不吹。

---

## 0. 一句话定位（防第一句被问穿）
> 本项目不是"接了一个 LLM 的聊天机器人"，而是一个 **TCMS 领域知识的受约束 agent 测试台**：
> LLM 可选、离线可复现、机器自证质量。它把 agent 的每一项能力都**挂到真实资产与可执行引擎上**，
> 并用 golden/对抗集/真实执行三重门禁证明——这正是"AI 测开 + Agent 工程化 + 安全关键域"岗位
> 叙事要的三块拼图。

---

## 1. Agent / Harness —— 真实参与，规则编排 + 可选 LLM 决策
| 维度 | 代码事实（证据） |
|---|---|
| 管线 | `agent/harness.py`：retrieve → plan → act → exec → verify → reflect → report，一条 TaskRun 带可审计 trace + 双轨 score |
| 决策后端可插拔 | `AgentBackend` 抽象；`LLMAgentBackend`（OpenAI 兼容 chat）/ `MockAgentBackend` 兜底；无 key 自动落 Mock（`llm_backend.py`） |
| 真实执行闭环 | Agent 决定后由**真实上游引擎**跑场景断言，不是 LLM 自说自话；run 结果沉淀进图 |
| 角色 | advisor / diagnoser / freeform / composer / reviewer 五个角色管线（多角色雏形，非多 agent） |
| 诚实口径 | **LLM 目前没有 function-calling**：工具编排由 harness 规则完成，LLM 只参与"规划/候选内排序/文案" |
| 缺口 | ① LLM 不能自主选工具（下一步：OpenAI function-calling，把 run_scenario/diagnose/kb_search 暴露成 tools）；② 单 agent，无多 agent 编排（DSH 的 agent-teams 是外借鉴对象，未入项目） |

## 2. 长对话 / 上下文 —— 从"无状态"到"会话化"，但尚未统一
| 参与点 | 证据 |
|---|---|
| 顾问多轮 | advisor/compose 收 `history` 做"点名续编"（composer.py:86-116） |
| 诊断多轮（本次新增 P1-1） | `session_id` + `diagnose_memory.AnchorMemory`：只存**证据引用**（symptom key/真实候选键/用户现象事实），不存散文摘要；追问（刚才/那个部位）复用锚点继续走链；TTL 过期清理 + 会话数上限 + 线程锁 |
| UI | AgentPage 诊断区带会话记忆与"⟲ 新会话"重置 |
| 诚实口径 | 记忆 = 锚点引用而非压缩摘要（防"摘要丢语义"）；无 token 预算管理 |
| 缺口 | 未做统一会话抽象（advisor/compose/diagnose 各自为政）、无上下文窗口裁剪策略 |

## 3. 记忆 —— 会话级 + 组织级（run 沉淀），双形态雏形
| 形态 | 证据 |
|---|---|
| 会话记忆 | AnchorMemory（见上） |
| 组织记忆 | `GraphSink.record_run` → 图 run 节点；本次 P2-3：`recent_runs` 参与**检索/诊断证据**（该资产真被跑过、passed/failed 可见、带出处 runtime:run_id）——"执行 100 次的场景对诊断有影响"落地为证据而非口号 |
| 诚实口径 | run 只关联场景节点；未做 run 文本/经验的向量化与长期记忆分层 |
| 缺口 | 记忆检索（experience RAG）、遗忘策略、跨会话 persona 记忆 |

## 4. 技能 / Tools —— 工具面就绪 + LLM function-calling（P1-a 已落地）
| 事实 | 说明 |
|---|---|
| 8 类内置任务 + FaultLab 场景库 + advisor 建议 | 等于把"能做什么"显式声明并锚定真实故障，漂移即失败 |
| 症状诊断 / KB 检索 / 场景执行 | 全部是 HTTP 端点 = 现成的"工具面" |
| ✅ **受约束 function-calling（P1-a，2026-09 已实现）** | `agent/toolassist.py`：kb_search / symptom_diagnose / kb_node / list_scenarios 四个**只读真实工具**，OpenAI 兼容 tools+tool_calls，≤3 轮循环；参数经 JSON 校验、未开放工具一律拦截、执行失败诚实回填、回复自证 used_tools；`POST /api/agent/toolassist`；6 条测试（假 LLM 编排/拦截/坏参/无 key 降级/纯工具真数据/HTTP 契约）。**无 key/失败 → llm_generated=false 确定性引导，绝不假装调用过工具** |
| 缺 | MCP 暴露（P1-b 进行中）；真正"自主执行"（run_scenario 级写工具）仍由 harness 守门（设计如此：执行不改由 LLM 直接触发） |

## 5. MCP —— ✅ 已落地（P1-b，2026-09，零第三方依赖）
- `agent/mcp_server.py`：stdio JSON-RPC 最小实现（initialize / notifications/initialized / ping /
  tools/list / tools/call + 错误码 -32700/-32601/-32602/-32603），不引入任何第三方库。
- 暴露 5 个工具：kb_search / symptom_diagnose / kb_node / list_scenarios（只读真实底座）＋
  run_scenario（执行类，需注入引擎 runner；默认未接线返回 isError=true 诚实说明，绝不假装执行）。
- 运行：`python -m tcms_ai_platform.agent.mcp_server`；任何 MCP client（DSH / Claude Desktop…）可直接指挥 TCMS 查证。
- 测试：7 例协议级 dispatch + stdio 端到端（真实子进程冒烟通过）。

## 6. RAG —— 深度参与且带门禁（本项目相对同行最硬的部分）
| 层 | 证据 |
|---|---|
| 三通道 | BM25 词法 + 向量 + **图谱证据**，RRF 融合（`retriever.py`） |
| 域路由 | 词表/图谱定位 → 域内有界 top-k → 不足诚实回退全库（mixed 标注） |
| 检索评测 | 14 条 golden 防回退（混合 14/14、纯向量 ≥13/14）；26 条诊断对抗集锁诚实 |
| 证据富化（本次 P1-4） | 命中带 `source_ref`（资产出处 file:key）、`anchor_stats`（邻接资产计数）、`weak_links`（2 跳可达弱证据，路径节点全真实不发明）——"无直接边也有可溯源弱关联" |
| RAG 澄清 | advisor/freeform 规则零候选 → KB 检索候选（"你可能指这些"），不 422 死路 |
| 诚实口径 | 默认向量 = 字符哈希；真语义为可选通道（P0-2）；这不叫"语义检索"，叫"字符级确定性通道 + 图谱显式边的 GraphRAG 风格混合检索" |

## 7. 图谱知识 —— 从"存图谱"到"用图谱推理 + 可审计"
| 能力 | 证据 |
|---|---|
| 本体 | 655 节点（enrich）：13 系统域分类 / 203 故障 / 104 场景 / 需求 / 功能 / threshold / interlock / hazard / 概念 |
| 因果 | 54 条 indicates/causes 边，逐条 basis（real_mechanism/derived）+ note + **出处 file:key（P2-1）** |
| 遍历 | `causal_chain` ≤3 跳取诊断候选、`shortest_path` BFS 逐边证据（图查询 /api/kb/path） |
| 端到端 | 症状(12) → 因果链 → 候选故障（真实字典键）→ 验证动作 → 复现场景 → run 记忆 → evidence 逐链可点到资产 |
| 诚实口径 | derived 边显式标注"示意推断，非字典明文"；对抗集锁不错域/不发明 |

## 8. 语义检索 —— 刚补上"可选真语义"，默认诚实降级
| 事实 | 证据 |
|---|---|
| P0-2 | `ApiEmbedder`：OpenAI 兼容 /embeddings，模型=显式/ENV/自动探测；失败逐条降级哈希；`api_active` 供上层判别 |
| 近义 golden | 5 对口语改写（灯闪→照明、受电弓→弓网…）条件执行：真通道下断言胜率/边际，否则 skip——**绝不把哈希余弦当语义证据** |
| 无 key 全绿 | 降级路径有独立测试（tests/test_api_embedder.py 7 例） |

## 9. 自证 / 评测体系 —— 面试主答案的落点
- 检索 golden 14 + 诊断 golden 8 + free 5 + **对抗集 26** + 置信度口径锁值（非概率）
- **真实执行作为最高裁判**：Agent 建议要真实引擎断言通过；reviewer 的 6 维是 KB 锚定规则评审（不是 LLM 自夸）
- run 记忆、出处链、弱证据都是"可打印证据"设计——诚实与可审计不是注释而是机器门禁
- 深层：`ai-testgen` 的变异杀毒 kill_rate / 反思自愈 / 双 judge 是"AI 写测试怎么证明好"的答案链

---

## 10. 面试应答要点（三句话讲完这个项目）
1. **它证明"AI 参与关键系统测试"不是聊天演示**：LLM 决策必须落到真实 TCMS 引擎的执行与 KB 锚定评审，
   错了能当场被断言与 golden 抓住（对抗集、真实 run、出处链是证据闭环）。
2. **我懂 agent 技术栈的每一层并诚实标深**：长对话做成了"证据引用式记忆"而非摘要聊天记录；
   RAG 是三通道+门禁；图谱知识从"存"走向"因果推理+出处";语义检索可选真通道、默认诚实降级；
   MCP/function-calling 是本项目当前**诚实标注的缺口**而不是假装有。
3. **质量观**：我给 AI 能力挂评测（golden/对抗/变异杀毒），给自己挂诚实口径文档（术语审计、
   置信度非概率、局限清单）——这正是 AI 原生测试与评测岗位要的"不被演示骗、能被数据证"。

## 11. 若要把"参与深度"再往上推一档（按 ROI）——进度跟踪
| 优先级 | 动作 | 状态（2026-09） |
|---|---|---|
| P1 | 给平台加 function-calling 后端（tools=kb_search/symptom_diagnose/kb_node/list_scenarios） | ✅ 已落地（toolassist + /api/agent/toolassist + 6 测试） |
| P1 | 平台暴露 MCP server | ✅ 已落地（mcp_server 零依赖 stdio + 7 测试 + 子进程冒烟） |
| P2 | 语义通道端到端验证脚本 | ✅ 已落地（scripts/verify_semantic_channel.py：配 key 输出近义增益，离线 exit=2 诚实引导） |
| P2 | 记忆分层：run 文本向量化 + 经验检索 | 留档（当前 run 以 recent_runs 邻接参与证据，够用即止，不追加向量化以免越界） |
| P2 | 检索加 cross-encoder 重排（可选） | 留档（小语料 644 条 + golden 门禁已够；10^5 量级再上） |
| P3 | 多 agent 编排（captain+角色成员） | 留档（本项目以多角色管线呈现，不引入多 agent 以免超出展示边界） |

---

## 12. 阶段收口与后续改进原则（2026-09，以问题驱动，不设“冻结”）
- **当前里程碑已达成**：全量 pytest **205 passed + 1 skipped**、ruff clean、vitest 16/16、
  图谱交互 e2e 5/5、CI（GitHub Actions）绿；HARDENING 11/11 + ROI P1-a/P1-b/P2 落地；
  文档与代码一致。
- **后续原则（2026-09 修订，撤销“新功能冻结”表述）**：不因“看起来完整”就停止——任何被
  用户/评审/使用中戳穿的缺口都优先修（例：Agent 答不了“仅告警/降级仍可运行”→ 已新增
  kb_filter_assets 与确定性枚举回退并接通 /agent/free）。同时守住边界：不造超出
  TCMS 资产与引擎范围、或无法机器自证的“镀金”功能。
- 技术上限/诚实边界不变：默认离线字符级检索（真语义=可选）、LLM 可选（无 key 全流程可跑）、
  run_scenario 需引擎 runner 接线、单角色管线不冒充多 agent。
- 已知后续候选（问题驱动，按需再排）：run 经验向量化、cross-encoder 重排、Agent 多轮工具
  记忆、图谱/资产浏览的更大数据集压力测试。
