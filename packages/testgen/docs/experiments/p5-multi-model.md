# P5 实验：多模型 × 多批正式对比（deepseek 家族 @ 百炼）

> 日期：2026-09-06；复现：`python examples/run_multi_model.py --models deepseek-v4-flash,deepseek-v3.2,deepseek-r1 --batches 3 --num 8 --mutation --out docs/reports/multi_model_v1.json`
> 补 README roadmap 首个 [ ]：「多模型 × 多批正式对比（v4-flash / v3.2 / r1）」。
> 环境：阿里云百炼兼容端点，temperature 0.3，固定请求模板与资产事实上下文。
> 上游：tcms-can-test（同一版本，真实 pytest 执行）。

## 1. 假设（延续 p3-source-comparison / p3-llm-real，docs/decisions.md D3）

H1（模型可区分）：同一 DSL + 同一真实执行管线下，不同 LLM 生成用例的
质量差异**可被量化指标捕捉**——parse/compile/exec + 变异杀毒在模型间
应有可见差异，且与「生成语义面广度」相关。

H2（稳定性可测）：批间方差（spread）反映模型输出的稳定性——质量评估
不能只看单批，多批中位数 + 极差才有意义。

H3（幻觉可归因）：真实执行器拦截的失败（EncodeError/IndexError/断言错）
可归因到具体模型与具体幻觉形态，量化「模型写测试的诚实成本」。

## 2. 设计（控制变量）

- 固定：百炼端点、temperature 0.3、prompt v2 契约（execution DSL 强制 +
  资产事实注入 + few-shot）、num=8/批、同一上游 tcms-can-test 版本、
  同一 3 变异库（行为翻转）。
- 变量：生成源模型（deepseek-v4-flash / deepseek-v3.2 / deepseek-r1）。
- 指标（docs/metrics.md）：parse_rate / compile_rate / exec_pass_rate
  （真实 pytest）+ 变异 kill_rate（相关用例分母，跨批 pooled）+ 幻觉数。
- 诚实边界：每批独立 LLM 调用（非固定 seed——LLM 端点无 seed 参数），
  n=3 批/模型，报告中位数与批间 spread，不宣称统计显著性。

## 3. 结果（2026-09-06 实测，3 批/模型，num=8/批）

### 3.1 主指标聚合（v4-flash 与 v3.2 带变异杀毒；r1 见 3.3）

| 指标（3 批） | **deepseek-v4-flash** | **deepseek-v3.2** |
|---|---|---|
| parse_rate | 0.875（0.75–1.0, spread 0.25）| 0.875（0.625–0.875, spread 0.25）|
| compile_rate | 1.0（全批稳定）| 1.0（全批稳定）|
| exec_pass_rate | **0.857**（0.833–0.875）| **1.0**（全过）|
| 幻觉被抓（真实执行拦截）| **5** | **0** |
| door_fault_ignored kill_rate | **1.0**（8/8）| **0.364**（4/11）|
| encode_never_rejects kill_rate | 0.875（7/8）| **1.0**（8/8）|
| overspeed_action_flipped | 1.0（1/1）| relevant=0（无相关用例）|

### 3.2 关键发现（H1/H2/H3 检验）

1. **H1 成立且有方向性**：v4-flash 的生成语义面显著更广——door 变异
   relevant=8（v3.2=11 但断言弱 4/11）、overspeed 有相关用例 1/1 全杀；
   v3.2 生成面窄（overspeed 无相关、door 断言只覆盖 36%）。**这是
   「生成源差异」在模型级的直接量化**：v4-flash 更敢组合场景、v3.2 保守。
2. **H2 成立**：parse_rate 批间 spread 两模型均 0.25——单批数字不可信，
   多批中位数 + 极差是必要口径（v3.2 第三批 parse 跌到 0.625，单批看会
   误判模型质量）。
3. **H3 成立且有价值**：v4-flash 5 个幻觉被真实执行器当场拦截（心跳
   IndexError ×2、编码 DID NOT RAISE EncodeError、方向溢出失败）——
   说明**生成面越广，幻觉风险越高**，exec_pass 0.857 vs v3.2 的 1.0 正是
   代价。这回答 README 遗留问题「exec 0.8~1.0 波动受幻觉影响」的机制：
   波动 = 生成面广度 × 幻觉率的乘积，而非纯随机。
4. **compile_rate 全 1.0 不区分模型**（延续 p3 发现：pass 类指标对简单
   生成源不敏感）——质量区分度集中在变异杀毒 + 幻觉两个维度。

### 3.3 r1（推理模型）——docs/reports/multi_model_r1.json（3 批，num=8，无变异）

| 指标（3 批） | **deepseek-r1** |
|---|---|
| parse_rate | 0.75（0.75–1.0, spread 0.25）|
| compile_rate | 0.944（0.833–1.0, spread 0.167）|
| exec_pass_rate | **0.833**（0.8–0.875）|
| 幻觉被抓（真实执行拦截）| **9**（最多：DID NOT RAISE EncodeError ×3、心跳枚举边界等）|

**r1 观察**：推理模型（thinking 链）在本 DSL 任务上**未见优势反见劣势**——
compile 首次跌破 1.0（0.833，某批输出格式漂移），exec 0.833 最低，幻觉
9 个最多（它倾向编造更复杂的边界场景，如「心跳枚举边界」但上游该枚举
并不拒绝）。成本还最高（推理慢，冒烟时单批曾触发 120s 超时，正式版
放宽 300s + 3 次重试后完成）。结论如实：**对受约束 DSL 填空任务，
推理模型不划算**——这与「LLM 负责广度选择而非自由创作」的设计判断一致。

## 4. 与既有基线对照（同管线，2026-09-06 实测）

| 生成源 | parse | compile | exec_pass | 变异覆盖 | 备注 |
|---|---|---|---|---|---|
| rule_baseline | 1.00 | 1.00 | 1.00 | 1/3 | 只会 encode 族 |
| mock_llm | 0.85 | 1.00 | 1.00 | 3/3 全杀 | DSL 一致性下界 |
| llm:deepseek-v4-flash | 0.875 | 1.00 | **0.857** | 3/3（door 1.0/encode 0.875/over 1.0）| 面广、幻觉 5 被拦 |
| llm:deepseek-v3.2 | 0.875 | 1.00 | **1.0** | 2/3（over 无相关）| 保守、door 弱 |
| llm:deepseek-r1 | 0.75 | 0.944 | **0.833** | （未跑变异）| 推理模型、幻觉 9 最多 |

**解读**：mock 是 DSL 一致性下界（3/3 全杀）——真 LLM 在 door/encode 上
都未能稳定达到 mock 的杀毒覆盖（v4-flash door 1.0 达到、encode 0.875
略低；v3.2 door 0.364 明显弱）。这说明**模板生成器的组合广度仍是上界**，
LLM 的增量在「自由选择场景」而非「覆盖所有行为」——与 p3-llm-real
结论一致且样本更大。

## 5. 结论（诚实边界）

1. 多模型对比可复现完成：**三模型质量差异被四维量化**（parse/compile/
   exec/幻觉 + 变异杀毒）：
   - **v4-flash**：语义面最广（door 1.0 / overspeed 1.0 杀毒），代价是
     幻觉 5 个、exec 0.857；
   - **v3.2**：最保守稳定（exec 1.0、零幻觉），但面窄（overspeed 无相关、
     door 断言弱 0.364）；
   - **r1**：推理链未见优势——compile 首次 <1.0、幻觉 9 个最多、exec
     0.833 最低、成本最高。
   - 结论：**对受约束 DSL 任务，非推理模型的「广度/稳定」权衡是关键
     维度，推理模型不划算**。没有「最好的模型」，只有「广度优先
     （v4-flash）vs 稳定优先（v3.2）」的使用场景选择。
2. **不宣称谁更好**：n=3 批/模型、无 seed 固定（端点不支持），数字有批间
   波动——已用 spread 如实呈现，不宣称统计意义。
3. v4-flash 的 5 个幻觉与 r1 的 9 个幻觉全部被真实执行器拦截——这再次
   证明「真实执行」环节的必要性（parse 层查不出域值/语义幻觉），是
   p3-llm-real H1 的更大样本复现。

## 6. 后续（backlog 更新）

- [x] 多模型 × 多批正式对比（v4-flash / v3.2 / r1 三模型 × 3 批）
- [ ] 更多模型扩展（qwen3.8-max / kimi / glm 同管线对照）
- [ ] 「生成面广度 vs 杀毒率」归因实验：控制同一批场景需求，看模型
      是否系统性偏向某些语义族
- [ ] prompt 补 door 断言形态 few-shot（v3.2 door 弱疑似 prompt 引导不足）
