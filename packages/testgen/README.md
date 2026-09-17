> ## 📦 仓库已合并 → tcms-ai-platform
> 本仓库（tcms-ai-testgen）已整体并入
> [**zych2002918/tcms-ai-platform**](https://github.com/zych2002918/tcms-ai-platform)，
> 代码位于其 **`ai-testgen/`** 目录，独立包结构保留（`pip install -e "./ai-testgen[test]"`）。
> 后续开发与 issue 请到新仓库。以下为原 README 存档。

---
# tcms-ai-testgen

[![Python](https://img.shields.io/badge/Python-3.10+-3776ab)](#)
[![tests](https://img.shields.io/badge/tests-165%20passed-2dd4a0)](docs/metrics.md)
[![coverage](https://img.shields.io/badge/coverage-91%25-2dd4a0)](docs/metrics.md)
[![CI](https://github.com/zych2002918/tcms-ai-testgen/actions/workflows/ci.yml/badge.svg)](https://github.com/zych2002918/tcms-ai-testgen/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**让测试平台学会自己写测试，并用量化证据证明它写得好。**

> 从真实 TCMS（列车控制）CAN 资产（DBC + 故障场景）生成结构化测试用例，
> 编译为**真实 pytest** 在 tcms-can-test 平台上执行，用四类指标 + **变异杀毒**
> 量化「AI 写的测试到底好不好」。
>
> 设计动机：作者此前手写了 [tcms-can-test](https://github.com/zych2002918/tcms-can-test)
> 的 960 个 pytest 用例（语句覆盖率 97.96%）。本项目回答一个问题：
> **如果让 AI 来写这些用例，我们如何证明它写得"好"？**
> —— 答案是把「质量评估」本身做成一条可复现的工程流水线，
> 并直面行业里普遍没答案的难点：**自然语言用例 ↔ 真实可执行代码的鸿沟**。

## 核心思路（第一性原理）

```
真实资产(DBC+场景) ──> 生成器 ──> execution DSL ──> 编译层 ──> 真实 pytest
   asset_loader        mock/真LLM   白名单原语       executor_real    在上游执行
                       (oracle派生) (机器可读)       (零自由代码)
```

**痛点与解法**：LLM 生成的自由文本几乎无法可靠编译成真实测试（P2 实证
compile≈0）。解法 = **受约束 execution DSL**：生成器输出白名单内的机器可读
执行意图（故障注入 / 信号注入 / 编码边界断言），期望一律由 **oracle**
（上游 faultlevel 语义镜像）派生——不手抄、不编造。同一 DSL 让 mock 与真实
LLM 走同一管线可比，也让「生成源质量」可量化对照。

## 质量证据（不靠嘴说）

| 证据 | 结果 | 复现 |
|---|---|---|
| 真实执行通过率 | mock 生成 34 条（requested 40，6 条故意残缺）→ 真实 pytest **34/34 passed**（compile 100%）| `examples/demo_full_loop.py --num 40` |
| **变异杀毒** | 3 个选定行为翻转（车门故障被吞 / 编码不拒越界 / 超速处置翻转）**全被杀死**（kill_rate 1.0，按相关用例分母）| 同上 `--mutation` |
| 生成源可区分 | 规则基线对 2/3 变异无感知（coverage 1/3）；mock 全覆盖 3/3——只报 pass_rate 会误判「两者都会写测试」| `examples/run_p3_comparison.py` |
| oracle 防漂移 | oracle↔上游 faultlevel 对拍测试（CI 必跑，键集/等级/处置全等）| `pytest tests/test_oracle_alignment.py` |
| **真 LLM 可执行** | deepseek-v3.2（阿里云百炼）生成 → compile 100%、真实 pytest 跑通；模型幻觉（AlarmLevel=-1）被真实执行器 EncodeError 当场拦截 | `examples/run_llm_arm.py`（需 DASH_API_KEY）|
| **AI 自我修正** | 反思闭环：失败 → 分类 → 带证据（oracle 域值 + RAG 金标）修正 → 重验。实测 22 条 5 失败 2 自愈（self-heal_rate 0.40）——规则/模板基线结构性做不到 | `examples/run_reflect_demo.py` |
| 语义鸿沟实证 | 首版车门断言按数字 2 真实执行失败（上游解码是 VAL_ 文本 'Fault'）——mock 启发式永远抓不到 | docs/experiments/p2-real-executor.md |

## 快速开始

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -e ".[test]"

# 30 秒自检（离线）
python scripts/selfcheck.py

# 真实资产摘要（tcms-can-test 的 DBC 22 报文/116 信号 + 104 场景；TCMS_UPSTREAM_ROOT 可指路径）
python examples/demo_assets.py

# 全链路一条命令：需求 → 生成 → 真实 pytest → 量化报告（附变异杀毒）
python examples/demo_full_loop.py --num 40 --mutation --out docs/reports/demo.json

# P3 生成源对比实验（规则基线 vs mock × 3 变异杀毒）
python examples/run_p3_comparison.py

# 真实 LLM（需 pip install .[llm] 与 DEEPSEEK_API_KEY；同一 DSL 同一管线）
python -m tcms_ai_testgen.cli --llm --target "TCMS 超速防护"

# P5 多模型 × 多批正式对比（v4-flash / v3.2 / r1；key 走 DASH_API_KEY 或 .credentials.yaml）
python examples/run_multi_model.py --batches 3 --num 8 --mutation --out docs/reports/multi_model_v1.json
```

## 模块地图

- `models.py` — 数据契约：`GenRequest` / `GeneratedCase`（含 execution 意图）
- `execution.py` — **execution DSL**：白名单原语（setup/expect）+ pydantic 严格校验
- `oracle.py` — 10 故障键语义镜像（level → action），生成期期望派生源
- `asset_loader.py` / `asset_models.py` — 真实资产（DBC 22 报文/116 信号 + 104 场景 YAML）
- `llm.py` — 客户端抽象：`MockLLMClient`（DSL 一致性套件）/ `OpenAICompatClient`（真实）
- `executor_real.py` — **真实执行器**：execution DSL → 真实 pytest（上游环境跑）
- `mutation.py` — **变异杀毒**：行为翻转 × 生成用例，精准 kill_rate
- `rag.py` — **RAG 金标索引**：上游 636 手写用例 → 检索（生成/修正证据）
- `agent.py` — **反思 harness**：失败分类 + diff 门禁 + 修正闭环（self-heal）
- `baseline_rule.py` — 规则基线生成源（对照臂，证明质量可区分）
- `pipeline.py` / `judge.py` / `executor.py` / `prompt.py` — 一期骨架（mock 质量信号）
- `cli.py` — CLI 入口

## 文档地图

- `docs/PLAN.md` — 成品蓝图与推进计划
- `docs/metrics.md` — 指标口径权威定义（parse/compile/exec/kill_rate 分母诚实）
- `docs/decisions.md` — 关键决策 + 对抗审查采纳记录
- `docs/asset-loader.md` — 资产解析设计
- `docs/experiments/p2-real-executor.md` / `p3-source-comparison.md` / `p5-multi-model.md` — 实验报告
- `docs/reports/` — 可复现运行报告（JSON）

## 工程门禁（与上游同款水准）

pytest 全绿（106+）· 覆盖率 ≥90% · ruff clean · selfcheck PASS · CI（3.10-3.12）
LLM 非确定性：CI 只跑 mock 离线；真实执行实验固定 seed/温度。

## Roadmap

- [x] P1 真实资产解析（DBC + 全量场景，坏文件诚实统计）
- [x] P2 真实执行器（execution DSL v1：关键词 → 真实 pytest）
- [x] P3a execution DSL（白名单原语 + oracle 派生期望，mock 可编译率 100%）
- [x] P3b 变异杀毒实验 + 生成源对比（质量证据）+ oracle 对拍防漂移
- [x] P3c 真 LLM prompt 契约（execution DSL 已入 system prompt + 资产事实注入）
- [x] P3d 真 LLM 臂实测（deepseek-v3.2：compile 100%、幻觉被真实执行拦截，见 docs/experiments/p3-llm-real.md）
- [x] P4 RAG（金标检索）+ 反思 harness（AI 自愈闭环，self-heal 0.40，见 docs/experiments/p4-agent.md）
- [x] P5 多模型 × 多批正式对比（v4-flash / v3.2 / r1 × 3 批，见 docs/experiments/p5-multi-model.md）
- [x] LLM-as-judge 与规则 rubric 交叉验证（agreement 1.0 / mean_abs_diff 3.6，见 docs/experiments/p4-agent.md §6）
- [ ] 多轮反思（round3+）与 class2 证据增强

## License

MIT

