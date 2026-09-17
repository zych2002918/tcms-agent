# 项目交接状态（HANDOFF）— 2026-09-05 收工

> 供明天/下次会话快速续接。仓库工作区干净；本文件 + docs/PLAN.md +
> docs/decisions.md 是权威状态。

## 1. 一句话现状

tcms-ai-testgen 二期已从「LLM 生成测试用例的 demo」推进到**带完整量化证据链
的 AI 测开工程成品**：真实 TCMS 资产 → 受约束 execution DSL → 真实 pytest →
变异杀毒 / 反思自愈 / 双 judge，全部实测可复现。**164 测试全绿、覆盖率
91.56%、ruff/selfcheck PASS、24 个原子 commit**。

## 2. 技术栈与关键路径

- 仓库：`E:\DSHworkplace\objects\tcms-ai-testgen`（venv: `.venv\Scripts\python.exe`）
- 上游被测平台：`E:\DSHworkplace\objects\tcms-can-test`（真实 DBC + 960 pytest + 仿真器）
- 真 LLM：阿里云百炼兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，
  key 走环境变量 **DASH_API_KEY**（用户持有，未入库）
- 演示：`python examples\demo_tour.py`（离线）/ `--llm`（含真 LLM 反思幕，已实测全绿）

## 3. 能力地图（模块 → 一句话）

| 模块 | 能力 |
|---|---|
| `asset_loader/models` | 真实 DBC(22报文/116信号) + 104 场景 YAML 解析，坏文件诚实统计 |
| `execution.py` | 白名单 execution DSL（setup/expect 原语，pydantic 校验）|
| `oracle.py` | 10 故障键 level→action 镜像（与上游 faultlevel 对拍测试防漂移）|
| `llm.py` | MockLLMClient（DSL 一致性套件）/ OpenAICompatClient（百炼）|
| `executor_real.py` | DSL → 真实 pytest → 上游执行（含枚举往返断言、编译期语义拦截）|
| `mutation.py` | 变异杀毒：3 行为翻转 × kill_rate（相关用例分母）|
| `rag.py` | 上游 636 手写用例金标索引 + 检索（生成/修正证据）|
| `agent.py` | 反思 harness：失败分类 + diff 门禁 + reflect_loop（self-heal_rate）|
| `judge_llm.py` | LLM-as-judge 与规则 judge 交叉验证（agreement/diff）|
| `baseline_rule.py` | 规则基线生成源（对照臂）|

## 4. 关键实测数字（全部可复现）

| 证据 | 数字 | 复现命令 |
|---|---|---|
| 真实执行 | mock 20-34 条 → compile/exec **1.0** | `demo_full_loop.py --num 40 --mutation` |
| 变异杀毒 | 3 变异 kill_rate **1.0**；rule 覆盖率 1/3 vs mock 3/3 | `run_p3_comparison.py` |
| 真 LLM 可执行 | v3.2 compile **100%**；幻觉(AlarmLevel=-1)被真实执行拦截 | `run_llm_arm.py` |
| AI 自愈 | 反思闭环 healed 案例；3 批 self-heal **0.40**（n 小）| `run_reflect_demo.py` |
| 双 judge | agreement **1.0** / mean_abs_diff 3.6（mock 源 7 条）| `run_judge_compare.py` |
| demo_tour | 全幕 exit 0；幕5 一次实测 2/2 healed | `demo_tour.py --llm` |

## 5. 明天可做的 backlog（按价值排序）

1. **多模型 × 多批正式对比**（v4-flash/v3.2/r1 × ≥3 批）→ 出正式对比表（百炼
   上 v4-flash 便宜，成本低）；这是 P3 报告「单模型小样本」缺口的补完。
2. **多轮反思**（round3+，失败原因累积）→ 提 class2 自愈率（当前单轮只修
   class1 稳）。
3. **interview_guide 演示章节补全**（demo_tour 五幕话术卡已口头梳理，待写档）。
4. **push 上游 GitHub**（repo 就绪；是否公开等用户决定）。
5. fault×mode 组合空间 / LLM-as-judge 真 LLM 源交叉（decisions D3 backlog）。

## 6. 提醒

- 用户 key `sk-ws-...` 敏感：只走环境变量，绝不写文件/commit。
- 若续接会话无上下文：先读本文件 + docs/PLAN.md + docs/decisions.md，再
  `python examples\demo_tour.py` 自检环境。
- 门禁三件套：pytest --cov-fail-under=90 / ruff / selfcheck（CI 已 checkout 上游）。
