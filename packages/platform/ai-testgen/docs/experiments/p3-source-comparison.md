# P3 实验：多生成源对比 + 变异杀毒（source comparison & mutation killing）

> 日期：2026-09-05（全数字实测可复现）
> 复现命令：`python examples/run_p3_comparison.py`（需上游 tcms-can-test）
> 假设 → 设计 → 指标 → 判定标准 见下；遵守 docs/metrics.md 口径与 docs/decisions.md D1。

## 1. 假设

H1（管线可区分生成源）：同一 DSL + 同一真实执行管线，不同「生成策略」的
产物质量差异**可被量化指标捕捉**——而传统 pass_rate 可能无法区分。
H2（变异覆盖是质量标尺）：只覆盖单一语义族的生成源（如纯编码边界）对
其它行为（车门/故障处置）的缺陷**无感知**（relevant=0），变异杀毒覆盖
（mutant_coverage）能暴露这种「看不见」。

## 2. 实验设计（控制变量）

- 固定：上游 tcms-can-test（同一版本）、同 DSL schema、同真实 pytest 执行器、
  同 3 个变异（行为翻转）、同 num_cases≈20。
- 变量：生成源。
  - **A rule_baseline**（baseline_rule.py）：机械规则——按 DBC 物理范围推导
    「边界±1」encode_bound 用例；无 oracle、无场景/故障语义。
  - **B mock_llm**（MockLLMClient v2）：DSL 一致性套件——encode_bound +
    simulate_inject + fault_scenario 三族，期望由 oracle（faultlevel 镜像）派生。
- 指标（docs/metrics.md）：parse_rate / compile_rate / exec_pass_rate（真实
  pytest）+ 变异杀毒：对每个变异算 relevant / killed / kill_rate（相关用例分母）
  + mutant_coverage（relevant>0 的变异数）。
- 判定标准：H1 成立 = 至少一个指标在 A/B 间显著差异；H2 成立 =
  mutant_coverage 差异明显且与「语义族广度」对应。

## 3. 变异库（对上游行为的安全翻转，不改上游源码）

| 变异 | 翻转行为 | 断言应覆盖的原语 |
|---|---|---|
| door_fault_ignored | set_door_state(state=2) 被吞成 0 | expect_signal(DoorControl.*Fault) |
| encode_never_rejects | db.encode_message 永不抛错、返回零帧 | expect_encode_error/ok（ok 含往返解码断言） |
| overspeed_action_flipped | faultlevel.action_for(overspeed)=emergency_brake | expect_action(overspeed) |

## 4. 结果（2026-09-05 实测）

| 指标 | A rule_baseline | B mock_llm |
|---|---|---|
| parsed / failures | 10 / 0 | 17 / 3（mock 故意残缺样本）|
| parse_rate | 1.00 | 0.85 |
| compile_rate | 1.00 | 1.00 |
| exec_pass_rate（真实） | 1.00 | 1.00 |
| **mutant_coverage** | **1 / 3** | **3 / 3** |
| door_fault_ignored: relevant/killed | 0 / 0 | 5 / 5（rate 1.0）|
| encode_never_rejects: relevant/killed | 10 / 10（rate 1.0）| 8 / 8（rate 1.0）|
| overspeed_action_flipped: relevant/killed | 0 / 0 | 1 / 1（rate 1.0）|

## 5. 结论

1. **H1 部分成立**：pass 类指标（compile/exec）两臂都是 1.00——encode_bound
   简单族不足以区分生成源；**区分度来自变异维度**（coverage 1/3 vs 3/3）。
   这本身是方法论发现：只看通过率的评测会得出「都会写测试」的错误结论。
2. **H2 成立**：规则基线对车门/超速两个变异的 relevant=0——它的用例「看不见」
   这两类缺陷（等价于测试套件根本没测这些行为）。mock 全覆盖并全杀
   （kill_rate 全 1.0）。变异杀毒覆盖 = 语义族广度的直接度量。
3. **质量证据**：mock 臂用例真实检出被测对象 3 个行为翻转（无一漏网），
   证明其断言非回声 spec 空壳；往返解码断言（encode_ok 补强）让
   encode 变异从 0.625 杀毒率升至 1.0——断言强度可量化改进。
4. **诚实边界**：本实验不含真实 LLM（环境无 key/本地模型，见 decisions.md
   D2）。结论限定为「该 DSL + 量化管线能区分生成源质量、能杀毒」；
   mock 臂数字不代表任何具体 LLM 的质量。真 LLM 走 OpenAICompatClient
   （同契约同管线——prompt v2 已强制 execution DSL 输出），接 key 即可
   补臂；补臂属实验执行，无需改管线结构。

## 6. 与 777 手写用例的关系（组合增量，诚实边界）

mock 臂 fault_scenario 族按 10 故障键 × 处置动作轮转，覆盖 faultlevel 全语义面
（info→none / minor→warning / major→derate / critical→emergency_brake）。
手写 777 例覆盖 13 场景 + 各模块行为深度；生成 DSL 提供的是**系统化的
fault × action 矩阵**（规则/机械方式难穷举的枚举组合）。

**边界（red-team D3 修正）**：上游 faultlevel 的模式敏感处置（major 在 rm
模式仅 warning）与 fault×mode×recover 笛卡尔组合**尚未实现**——oracle/mock/
executor 三层当前只支持 auto 模式一列。任何「mode 维度组合增量」的表述均为
空头支票；组合空间扩展记 backlog（需 oracle 加 mode 列 + DSL 加 mode 参数 +
编译层透传），届时对上游 rtm.csv 做集合差量化。
