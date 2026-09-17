# 质量指标口径（Metrics Definition）

> 权威定义：一切实验报告的指标口径以此为准。**分母含残缺样本**，禁止挑
> 对自己有利的样本集凑指标——「质量可度量」的前提是口径诚实可复现。

## 1. parse_rate —— 结构化解析率

LLM 生成侧（GenReport）：
```
parse_rate = 成功结构化解析的用例数 / (解析成功 + 解析失败)
```
- 失败 = LLM 输出无法解析成 GeneratedCase（含缺 name/purpose/expected、
  非 dict、JSON 提取失败）。整批提取失败计 1 次失败。
- mock 客户端故意注入残缺样本，使该指标有区分度（实测 ~0.875，非 1.0）。

真实资产侧（AssetBundle.stats）：
```
parse_rate = (parsed_yaml + parsed_dbc) / (parsed + bad)
```
- bad = 文件不存在 / 空 / YAML 语法错 / 编码错 / 目录缺失（计 1）；
- **空目录 ≠ 坏文件**（合法空集），不参与分母；
- 行级坏行（DBC raw_line_errors / 场景 skipped_steps）不降文件解析状态，
  但必须在报告里显式可见（demo_assets 打印清单）。

## 2. exec_pass_rate —— 真实执行通过率

二期（DSL 时代，现行口径）：真实 pytest —— 生成用例编译后在 tcms-can-test
上游环境执行（executor_real.run_real）：
```
exec_pass_rate = 真实 pytest 通过用例数 / 实际可编译用例数（compiled）
compile_rate   = 可编译用例数 / 总生成用例数（含无 execution 者）
```
- 分母 = 编译成功（compile_case 非 None）的用例数；无 execution / 语义不可
  编译的用例不计入分母，但通过 compile_rate 反映（生成质量含"能不能跑"）；
- 一期遗留的启发式执行器（executor.py run_deterministic）只作离线质量信号，
  不再是 exec_pass_rate 的权威来源（pipeline 内 mock 口径保留仅为自检展示）；
- LLM 非确定性：CI 用 mock/录播，真实执行实验固定 seed/temperature。

## 2b. kill_rate —— 变异杀毒率（质量证据，docs/experiments/p3）

```
kill_rate = 被变异杀死的相关用例数 / 相关用例数（relevant）
```
- 相关用例 = 其断言覆盖该变异翻转行为的用例（mutation_relevant 判定）；
- 分母**只用相关用例**：不相关用例在变异版上 survive 属正常，不算漏网；
  附带报告 overall_kill_rate（分母=全部）仅供对比；
- 变异 = 对上游真实行为的受控翻转（不改上游源码，patch 注入生成文件）：
  door_fault_ignored / encode_never_rejects / overspeed_action_flipped；
- **口径红线**：只统计「原始版 PASS 且变异版 FAIL」的用例为 killed——原始
  就失败的用例不算杀毒（它没证明断言力）。

## 2c. self_heal_rate —— 反思自愈率（P4 质量证据，docs/experiments/p4-agent）

```
self_heal_rate = healed / (healed + not_progressed)
```
- healed = round1 真实执行 FAIL → 带证据修正 → round2 PASS 的用例数；
- not_progressed = FAIL 且修正后仍 FAIL（含修正被 diff 门禁拒绝 / 不可编译 /
  无证据可修）；
- 分母**不含** stable（本来就 PASS 的用例不参与——它们无需自愈）；
- **diff 门禁红线**：修正须与失败版 execution 实质不同且不删断言，否则计
  not_progressed（防「换说法假装自愈」）；修正版不可编译 → 丢弃计
  not_progressed（防「删失败项假装自愈」）；
- 实测基线：deepseek-v3.2 三批 22 条 / 5 失败 / 2 healed → 0.40（n 小，
  仅证明机制可行，无统计意义）。

## 2d. judge 一致性（P4-C，docs/experiments/p4-agent §6）

```
mean_abs_diff = Σ|rule_score - llm_score| / n      （同 0-100 量纲）
agreement_rate = |diff|<=15 的用例占比
```
- LLM judge 额外给语义可执行性维度（规则 judge 没有）：枚举信号文本断言 /
  encode 值域 / fault 键已知集——LLM 低分必给 reason；
- 实测基线（mock 源 7 条）：agreement 1.0 / mean_abs_diff 3.6；分歧最大
  用例 diff=15（规则满分 vs LLM 因 covers 空扣分）暴露规则盲点。

## 3. quality_score —— judge 质量分（0-100）

规则 rubric 评分（judge.py）：结构完整度 + 需求可追溯 + 场景设计
（边界/异常/否定）。一期实测 ~94。二期 P4 与真 LLM-as-judge 交叉验证：
一致性 ≥0.7（或给出差异分析），实测见 2d。

## 4. 通用纪律

- 每个数字必须能复现：对应命令/文件写进 docs/experiments/ 或本节；
- 实验一律控制变量：一次只改一个变量，其余固定并记录；
- 覆盖率口径：pytest-cov `--cov-fail-under=90`（branch=true，source=tcms_ai_testgen）；
- **质量 ≠ pass_rate**：通过率只是「能跑」，断言力须看 kill_rate/变异覆盖
  （p3 实验实证：两臂 pass 均 1.0 但 mutant_coverage 1/3 vs 3/3）。

## 5. 实测基线（随阶段更新）

| 指标 | 值 | 复现命令 |
|---|---|---|
| parse_rate（mock 生成，num=40） | 0.85（含故意残缺） | `python examples/demo_full_loop.py --num 40` |
| compile_rate / exec_pass_rate（mock→真实） | 1.0 / 1.0（34/34） | 同上 |
| kill_rate（3 变异，相关口径） | 1.0 / 1.0 / 1.0 | 同上 `--mutation` |
| 生成源区分度 | mutant_coverage 1/3 vs 3/3 | `python examples/run_p3_comparison.py` |
| parse_rate（真实资产） | 1.0（1 DBC + 104 场景） | `python examples/demo_assets.py` |
| 测试门禁 | 165 passed / 91.45% / ruff / selfcheck | `pytest tests --cov=tcms_ai_testgen -q` |
