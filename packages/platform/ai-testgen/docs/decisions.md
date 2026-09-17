# 决策记录（DECISIONS）

> 记录关键方向决策与子 agent 对抗审查结论，遇阻塞/改范围先记这里再行动。

## D1（2026-09-05）execution DSL 主线 — 采纳对抗审查

**子 agent 审查结论**（A-F 全节）核心：
- A 支持：瓶颈是自然语言↔可执行语义信道；DSL = 最小闭合语义接口。
  产品定位 =「LLM 从资产选场景 + 组合原语，断言由上游编解码语义作 oracle；
  创作归系统，LLM 负责广度选择」。
- B 修正：execution 是**唯一语义源**（purpose/steps 派生，防双轨漂移）；
  枚举双向表示须类型区分（encode 输入 raw 2 / decode 断言 VAL_ 文本 'Fault'）。
- C v1 最小原语：set_speed/set_handle/set_door_state/stop_message/send_alarm +
  expect_encode_error/expect_encode_ok/expect_signal/expect_signal_lost。
- E 两风险：范围失控；「模板填空」质疑 → 用变异杀毒实验应答。
- F 实验范式：**变异杀毒**（翻转 2-3 个 simulator 行为看生成用例能否抓到），
  固定任务=场景子集×金标；指标 parse/compile/pass + 变异杀毒率。

**采纳**：
1. execution 为唯一语义源：mock 的 purpose/steps 由模板 execution 派生（已如此）；
   GeneratedCase 校验要求带 execution 且 is_executable。
2. 枚举类型区分：DSL args 文档化——expect_signal 的 equals 若是字符串=VAL_ 文本，
   数字=raw（由编译层照原样生成，运行期 oracle 判等）。
3. P3 实验采用「场景金标 + 变异杀毒」设计，不只报通过率。
4. 范围控制：单族（车门/车速/心跳）端到端先通，不铺多族。

**驳回**：B 的 stages 线性时序段（setup+wait+assert 已够 v1，虚拟时钟是上游
ScenarioRunner 的事，我们的注入-采集窗口用 wait_ms 已覆盖心跳跨周期）；
full 自由代码生成（P2 实证 + 安全）。

## D2（2026-09-05）P3 无真 LLM 的实验立场

环境无 API key/本地模型（实测）→ P3 用「生成源对照」：规则基线 vs MockLLM vs
手工（上游 777 例抽样金标）。**不声称任何 LLM 质量数字**，结论限定为
「该 DSL+管线能区分生成源、能通过变异杀毒检出 sim 行为翻转」，真 LLM 通道
（OpenAICompatClient）接 key 即用，文档诚实标注。

## D3（2026-09-05）Red-team 终审处置记录

终审发现 3 高 + 2 中 + 1 低 + 777 叙事硬伤。处置：

| # | 发现 | 处置 |
|---|---|---|
| 高1 | oracle 静态镜像与上游可能漂移且零对拍 | ✅ tests/test_oracle_alignment.py：CI checkout 上游后 subprocess 对拍键集/等级/LEVEL_ACTION（实测通过）|
| 高2 | 真 LLM prompt 无 execution 契约 → 永不产出可编译用例，「接 key 即补臂」夸大 | ✅ prompt.py v2：execution DSL 契约 + few-shot + raw/decoded 方向规则 + 资产事实注入（build_asset_context）；README/guide 表述同步改「契约已就绪，接 key 即同管线」|
| 高3 | kill_rate 自证：变异集只覆盖 mock 恰好断言的面 | ⚠️ 文档诚实：结论限定「3 个被选中行为翻转全杀」；relevant=0（未测）≠ 0 分，报告明示；变异面扩展（心跳/叠加/mode）记 backlog |
| 中4 | fault_scenario 纯查表（回声风险）| ⚠️ 文档定位：fault_scenario 断言 = 处置逻辑契约测试（oracle 与上游 faultlevel 一致性由对拍守）；不宣称测平台运行时行为；真场景链（ScenarioRunner）注入记 backlog |
| 中5 | exec_pass 分母=compiled 非 requested | ✅ 口径已写 metrics.md；README「34/34」补注 requested=40 |
| 777 | 「fault×mode 组合增量」空头支票（三层均不支持 mode）| ⚠️ 实验报告改写：当前增量=10 键 × 处置动作矩阵 + 信号断言族；mode 维度的组合增量删除，记 backlog |

**可宣称的最大结论（一句话）**：构建了一条可复现的量化管线，证明「受约束
DSL + oracle 派生 + 真实执行 + 变异杀毒」能区分生成源质量（规则基线
mutant coverage 1/3 vs mock 3/3）——mock 臂仅代表该模板生成器，不代表任何 LLM。
**必须避免宣称**：真 LLM 质量数字（未跑）；「kill_rate 1.0 证明断言力」（仅对
3 个被选中变异）；「组合增量含 mode 维度」（未实现）。

## D4（2026-09-05）CI 集成上游

CI checkout zych2002918/tcms-can-test 到工作区平级并装运行依赖，使真实执行/
变异/对拍测试参与覆盖率（本地无上游时 cov 81% 属已知降级，README 已注明）。

## D5（2026-09-05）真 LLM 臂实测（deepseek-v3.2 @ 阿里云百炼）

用户提供百炼 key（sk-ws- 开头）后接入：
- OpenAICompatClient + asset_context（asset_prompt.build_prompt_context 从
  AssetBundle 自动构造 DBC 枚举/范围事实）→ prompt v2 契约实测有效：
  **真 LLM 产出 100% 可编译 execution**（red-team 高2 修复的验收）；
- 真实执行器抓到模型幻觉：send_alarm AlarmLevel=-1（编造域值）→ EncodeError
  当场拦截——「真实执行」环节必要性的活证据（parse 查不出域值幻觉）；
- 杀毒随批次波动（encode 全杀、door 窄、overspeed 可能缺席）→ mock 是
  DSL 一致性下界，LLM 增量需多批实测，单批数字不宣称优劣（报告
  docs/experiments/p3-llm-real.md）；
- prompt 已补 send_alarm level ∈ 0-3 显式约束减少该类幻觉；
- **安全**：key 只走环境变量（DASH_API_KEY），不落任何仓库文件；用户可随时
  在百炼控制台轮换。

## D6（2026-09-05）P4 RAG + 反思 Harness（对抗审查采纳 + 实测）

用户要求把 AI 测做深（RAG/工具/harness）。对抗审查（subagent）核心裁定全部采纳：
1. **diff 门禁防退化**：修正须与失败版 execution 实质不同且不删断言，否则
   unhealed（防「换说法假装自愈」）；
2. **证据分层**：class1 域值幻觉靠 oracle 域值事实；class2 断言错配靠 RAG
   金标；class3 目标不支持 → 计 replaced 不算 healed（RAG 零命中是判据）；
3. **mock 注入臂为主证据**（离线可复现），真 LLM 1-2 批仅 demo；
4. 只修失败项 + 全量重跑（按 name 归因，禁改坏已过项）。

实测（deepseek-v3.2）：
- class1 healed 实证：AlarmLevel=-1 → oracle 证据 → 修到 0 → round2 PASS；
- 3 批 × 8 条：22 生成 / 5 失败 / 2 healed / 3 np → **self-heal_rate 0.40**；
- 枚举信号 encode 传文本（'Emergency'）是 LLM 新幻觉形态 → executor_real
  编译期拒绝（字符串 value → 不可编译计 compile_rate 缺口，不崩溃）；
- expect_encode_ok 往返断言修复：枚举信号 decode 是 VAL_ 文本，raw 比较
  假失败 → 编译器按 ENUM_SIGNAL_TEXTS 映射文本断言。

「AI 自我修正」= 规则/模板基线结构性做不到的指标（self-heal_rate），
AI 测开叙事升级点。class2 单轮自愈率低 → 多轮反思记下一步。
