# tcms-ai-testgen 二期 — 最终成品 PLAN（全权推进版）

> 状态：2026-09-05 起，P1/P2 已交付（commit 2e39c4c / 05fe302）。
> 本文件定义「最终成品形态」与推进计划，是后续所有工作的裁判依据。

## 1. 最终成品形态（验收蓝图）

**使命**：让 TCMS 测试平台学会自己写测试，并用量化证据证明它写得好。
**证据链**：真实资产 → 生成 → 编译 → **真实 pytest** → 量化报告。

### 成品 = 一条可复现命令 + 一份可信报告 + 一套面试话术

```
tcms-ai-testgen --assets <upstream> --source <generator> --real  →  docs/reports/<run>.json/md
```
（需求/资产 → 生成(受约束 execution DSL) → 编译真实 pytest → 上游执行 → parse/compile/exec/quality 全指标）

### 交付物清单（对照章程成功标准）

| 章程成功标准 | 交付物 | 状态 |
|---|---|---|
| 真实跑通「需求→生成→执行→报告」全链路，数字实测可复现 | `examples/demo_full_loop.py` + `docs/reports/` | P2 完成雏形，需 DSL 打通 |
| ≥1 份多生成源对比实验报告（数据表+结论） | `docs/experiments/p3-source-comparison.md` | P3 待做 |
| 工程水准：测试全绿、cov≥90%、ruff、CI | pytest 全绿 + 门禁 | 维持 |
| 面试 3 分钟可讲 | `docs/interview_guide.md` + README 故事线 | 待做 |

### 核心架构故事（面试主线）

```
┌────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
│ 真实资产    │ → │ 生成器        │ → │ 编译层         │ → │ 真实执行      │
│ DBC+场景   │   │ mock/真LLM    │   │ 白名单原语→    │   │ 上游 pytest   │
│ (asset_    │   │ 输出 execution│   │ 真实API调用    │   │ (tcms-can-   │
│  loader)   │   │ DSL(受约束)   │   │ + 期望参考表    │   │  test)       │
└────────────┘   └──────────────┘   └───────────────┘   └──────────────┘
    事实边界         受约束生成           语义落地             真实验证
```

**第一性原理**：唯一要回答的问题是「如何量化证明 AI 写的测试是好的」。
断点 = 自然语言 ↔ 可执行代码鸿沟（P2 实证）。主线 = 用**受约束 execution DSL**
弥合鸿沟，使生成器输出机器可读、白名单内的执行意图，让全链路数字可信。

## 2. 阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| R1 审查 | 子 agent 对抗审查成品定义/DSL 方向 | 有书面结论，采纳/驳回记录 |
| R2 DSL | models 加 `execution` 字段；prompt 白名单原语+期望参考表；mock 生成 DSL | ✅ 单测绿，mock 全链路 compile_rate 100%（commit 9d394a3）|
| R3 闭环 | demo_full_loop 一条命令出报告 | ✅ mock-34 真实执行 34/34 passed（commit 7b7ae02）|
| R4 实验 | P3 生成源对照（规则/mock）+ 变异杀毒 + 数据表 + 结论 | ✅ mutant_coverage 1/3 vs 3/3（commit 99260ff）|
| R5 文档 | interview_guide + README + metrics 更新 | ✅（commit 7b7ae02，red-team 后修订 b0743cc）|
| R6 终验 | 子 agent red-team 审查 + 章程逐条核验 + 门禁 + commit | ✅ red-team 3 高全修（prompt 契约/对拍/诚实文档）；118 passed / 91.99% / ruff / selfcheck（commit b0743cc）|

## 4. 终验结论（2026-09-05）

- 章程成功标准逐条达成：全链路实测可复现 ✓；P2/P3 实验报告 ✓；工程门禁全绿 ✓；
  interview_guide 3 分钟话术 ✓。
- red-team 终审 3 高 + 2 中 + 777 叙事硬伤全部处置（docs/decisions.md D3），
  成品宣称收敛为可辩护的一句话（见 D3「可宣称的最大结论」）。
- 遗留 backlog（有意不本期做）：真 LLM 对比臂（prompt 契约已就绪）、
  fault×mode×时序组合空间、真 LLM-as-judge、变异面扩展（心跳/叠加/mode）。

**约束**：离线优先（无 API key / 本地模型 → 用 mock+规则+手工对照，诚实标注）；
成本受控（子 agent 只用于方向审查与终验，实现主链本人做）；每阶段先自测后回报。

## 3. 已探测事实（2026-09-05）

- 无 ollama / llama-server / LM Studio / vLLM；SEE_BASE、LLAMA_HOST、LLM_BASE 全空
- DEEPSEEK_API_KEY / OPENAI_API_KEY 未设置；tcms-ai-testgen venv 未装 openai
- → 真 LLM 本轮不可用；OpenAICompatClient 通道保留，文档标注「接 key 即用」
- 上游 tcms-can-test .venv 可跑 pytest（smoke 70 passed/1.8s 实测）
- 上游 API 事实（DSL 白名单来源）：simulator.set_speed/set_handle/set_door_state/
  stop_message/send_alarm；parser.collect/count_frames；db.encode_message（EncodeError）
- 真实解码枚举为字符串：Door2State=='Fault'、Direction=='Forward'（P2 实证）
