# P4 实验：RAG + 反思 Harness（AI 测开闭环）

> 日期：2026-09-05；复现：
> `python examples/run_reflect_demo.py --num 8 --out docs/reports/reflect_bN.json`（需 DASH_API_KEY）
> mock 闭环（离线）：`pytest tests/test_agent.py -q`
> 设计经对抗审查（docs/decisions.md D6），含 diff 门禁防「换说法假装自愈」。

## 1. 假设

H0（闭环机制）：失败用例带证据修正后重跑，能产生 FAIL→PASS（healed）。
H1（证据必要性）：无证据（oracle/RAG）时无法修复——修正依赖证据注入。
H2（诚实性）：self-heal_rate 非 1.0——LLM 修正不稳定，diff 门禁能拦截
「原样返回/删断言/改对象」等假自愈。

## 2. 设计

- 两轮闭环：round1 真实执行 → 失败分类（class1 域值幻觉 / class2 断言错配 /
  class3 目标不支持）→ LLMReflector 修正（失败详情 + oracle 信号域值 +
  RAG 上游金标，≤1 次修正调用）→ round2 全量重跑。
- 防作弊门禁（审查采纳）：execution_diff（新旧实质不同且不删断言）；
  修正不可编译 → 丢弃计 not_progressed；改名/空输出 → 拒绝。
- 指标：self-heal_rate = healed / (healed + not_progressed)；逐例状态机
  stable / healed / not_progressed / regressed。
- 对照：MockReflector（确定性钳制 class1，离线）证明机制；
  真 LLM（deepseek-v3.2）证明实际自愈能力。

## 3. 结果（2026-09-05 实测）

### 3.1 mock 臂（离线，机制验证）

```
构造 AlarmLevel=-1（expect_encode_ok）→ round1 FAIL
MockReflector 钳制到 0（有 oracle 事实）→ round2 PASS（healed）
oracle_facts 为空（无证据）→ 无法修 → not_progressed（H1 成立）
```

### 3.2 真 LLM 臂（deepseek-v3.2，3 批 × 8 条）

| 批 | 生成 | round1 失败 | healed | not_progressed | self-heal_rate |
|---|---|---|---|---|---|
| b1 | 8 | 2 | 1 | 1 | 0.50 |
| b2 | 7 | 2 | 0 | 2 | 0.00 |
| b3 | 7 | 1 | 1 | 0 | 1.00 |
| **合计** | **22** | **5** | **2** | **3** | **0.40** |

失败类型观察：class1（域值幻觉如 AlarmLevel=-1/文本填枚举）在 oracle
域值证据下**可修**（healed 案例 = 修正 value 到合法 raw）；class2（断言错配）
单次修正常不够（not_progressed 主体）——符合审查预判「RAG 对 class2 的
作用需更精准证据，可能需多轮」。

## 4. 结论（诚实）

1. **H0 成立**：真实 AI 自愈存在——v3.2 收到失败详情 + 域值证据后，把
   `AlarmLevel=-1` 修为合法值，round2 真实执行 PASS（transition=healed）。
2. **H1 成立**：mock 臂无证据不愈（not_progressed）——证据是修复的必要条件。
3. **H2 成立**：0.40 整体自愈率如实反映 LLM 修正不稳定；diff 门禁拦截了
   原样返回/删断言等假自愈（测试覆盖）。
4. **边界**：n=5 失败样本太小，0.40 无统计意义（只证明机制可行且有真实
   波动）；class2 自愈率低说明「修正一轮不够」——多轮反思/更强证据是
   下一步；不宣称「AI 能修好大部分自己的测试」，只宣称「失败→带证据
   修正→重验」的闭环已可运行、可量化、有真实 healed 案例。

## 5. 与基线/前几期的关系

| 能力 | rule_baseline | mock 生成 | LLM 生成 | +反思闭环 |
|---|---|---|---|---|
| 写用例 | 只会 encode | 全族模板 | 全族自由组合 | 同左 |
| 犯错后自改 | ❌ 不可能 | ❌ 不可能 | ❌（原管线）| ✅ healed 0.40 |
| 质量证据 | pass+kill | pass+kill | pass+kill | +self-heal_rate |

「AI 会自我修正」是本项目第一次出现**规则/模板基线结构性做不到**的指标——
这是 AI 测开叙事从「生成器」升级到「主体」的关键证据。

## 6. P4-C LLM-as-judge 交叉验证（2026-09-05 实测）

规则 judge（judge.py，结构+可追溯+场景词）vs LLM judge（同 rubric +
语义可执行性维度，deepseek-v3.2）：

```
复现：python examples/run_judge_compare.py --source mock_llm --num 8
mock 源 7 条：agreement_rate = 1.0（|diff|<=15 全一致）
             mean_abs_diff   = 3.6
```

观察：
1. **高度一致但 LLM 更严**：规则给满分 100 的 `test_speed_boundary_ok_2`
   LLM 只给 85（covers 为空扣追溯分）——diff=15 是最大分歧，暴露规则
   judge 盲点（mock encode 族 covers 空未被规则捕获）。
2. **LLM judge 提供语义理由**：reason 显示它真在检查执行语义
   （"state:2 对应 Fault"、"200.1 超上限 200"、"fault 在已知集"）——
   这是规则 judge 给不出的维度，也是 judge 交叉验证的价值。
3. 诚实边界：n=7 单批、mock 源（真 LLM 源 judge 需另跑），不宣称
   统计结论；只证明「规则+LLM 双 judge 管线可用、差异可量化」。

## 7. 下一步

- 多轮反思（round3+，带上一轮失败原因，限轮次防烧钱）；
- class2 证据增强：RAG 返回「同信号真实断言写法」而非泛化金标；
- 修正成本/收益指标（一次修正 ≈ 2 次 LLM 调用 vs 人工改一行）；
- P4-C LLM-as-judge 交叉验证。
