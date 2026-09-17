# P3-LLM 实验：真实 LLM 臂（deepseek-v3.2 @ 阿里云百炼）

> 日期：2026-09-05（真 LLM 首次接入；数字全部实测可复现）
> 复现：`set DASH_API_KEY=sk-...` 后
> `python examples/run_llm_arm.py --model deepseek-v3.2 --num 8 --mutation --out docs/reports/llm_real_v32.json`
> 端点：阿里云百炼兼容模式 `https://dashscope.aliyuncs.com/compatible-mode/v1`

## 1. 假设（延续 p3-source-comparison，docs/decisions.md D3）

H0（契约有效）：prompt v2 强制 execution DSL 输出后，真 LLM 生成的用例
应**可编译**（compile_rate 显著 >0）——验证「真 LLM 走同管线」不是空话。
H1（管线拦截幻觉）：真 LLM 偶尔编造非法域值（如信号越界、枚举错），真实
执行器应能**拦截并暴露**（exec_pass <1 + 具体 EncodeError）——这是
「LLM 写测试必须真实验证」的活证据。
H2（杀毒可比）：真 LLM 用例的变异杀毒与 mock 对照，暴露生成策略差异。

## 2. 设计

- 固定：百炼 deepseek-v3.2、temperature 0.3、prompt 注入资产事实（DBC
  枚举/范围 + 10 故障键）、同一 parse/compile/真实 pytest/变异管线。
- 变量：仅「生成源 = 真 LLM」（对照 p3 实验的 rule_baseline / mock_llm）。
- 指标：parse_rate / compile_rate / exec_pass_rate / kill_rate（同口径）。

## 3. 结果（实测）

### 3.1 批次 A（num=5，首探）

```
parsed=4/5  compile=4/4 (100%)  —— 4 条全部带合法 execution
```

**第一批即证明 H0**：prompt v2 契约让 deepseek-v3.2 产出 100% 可编译的
execution DSL（red-team 高2 修复的验收：真 LLM 不再空转）。

### 3.2 批次 B（num=6）

```
parsed=5/6  compile=5/5 (100%)
real exec: 4 passed / 1 failed
FAILED: test_overspeed_alarm_generation — EncodeError: Expected "AlarmLevel"
value >= 0 ... but got -1
```

**H1 实证**：模型为 `send_alarm` 编造了 `AlarmLevel=-1`（非法，真实枚举是
Info/Warning/Severe/Emergency 0-3）——真实执行器以 EncodeError **当场拦截**。
这正是「LLM 幻觉必须用真实执行暴露」的论据：parse 层只能查结构，编造域值
只有真实编码/解码能抓住。同一批变异杀毒：encode 变异 2/2 全杀、door 1/2、
overspeed 0/1（被杀那条本身就是幻觉用例）。

### 3.3 批次 C（num=8，存档 docs/reports/llm_real_v32.json）

```
parsed=7/8 (0.875)  compile=1.0  real exec: 7/7 passed（本批无幻觉）
变异杀毒：encode_never_rejects 4/4 全杀（rate 1.0）
         door_fault_ignored    1/3（模型 door 断言面窄于 mock）
         overspeed_action_flipped relevant=0（本批未生成 overspeed 处置用例）
```

## 4. 三臂对照（同管线，2026-09-05 实测）

| 生成源 | parse | compile | exec_pass | 变异 coverage | 备注 |
|---|---|---|---|---|---|
| rule_baseline | 1.00 | 1.00 | 1.00 | 1/3 | 只会 encode 族 |
| mock_llm | 0.85 | 1.00 | 1.00 | 3/3 全杀 | DSL 一致性套件 |
| llm:deepseek-v3.2 | 0.875 | **1.00** | 0.8~1.0（波动） | 依赖生成内容 | 会幻觉（AlarmLevel=-1 被拦）|

## 5. 结论（诚实边界）

1. **H0 成立**：prompt v2 契约有效——真 LLM 产出 100% 可编译 execution，
   「mock 与真 LLM 同契约同管线」从设计变为实测。
2. **H1 成立且有价值**：真 LLM 会编造非法域值；真实执行器拦截并给出
   EncodeError 证据。这反过来证明本项目「真实执行」环节的必要性——
   纯 parse 校验无法发现域值幻觉。
3. **H2 部分成立**：真 LLM 杀毒覆盖取决于该批生成内容（encode 全杀、
   door 断言窄、overspeed 可能缺席）——**比 mock 不稳定**。这正是「mock 是
   DSL 一致性下界、LLM 增量需实测」的量化注脚。
4. **不夸大**：单模型单温度少数批次，数字有波动（exec 0.8~1.0 受幻觉
   影响）；不宣称「v3.2 比 mock 好」。多模型 × 多批次 × 固定 seed 是
   正式对比的前置，本实验只证明**管线对真 LLM 可用且能给出质量证据**。

## 6. 后续

- 多模型对比（deepseek-v4-flash / v3.2 / r1 × 多批）→ 正式 p4 报告；
- prompt 补 send_alarm level ∈ 0-3 的显式约束（减少该类幻觉）；
- 幻觉自动归类（域值幻觉 vs 结构幻觉）作为新指标。
