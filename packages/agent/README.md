# tcms-agent —— TCMS 全流程测试工程师 Agent

> 用 LangGraph 编排的领域 Agent：在**真实**列车控制测试资产上查证、并让每一步
> 都可审计、可回放、可机器校验。

本包是 `tcms-agent` monorepo 的认知层，位于 `packages/platform`（知识底座）
与 `packages/engine`（领域引擎）之上。

---

## 快速开始

```bash
# 在仓库根（会自动装好 engine/platform/agent 三个成员）
uv sync

# 自检：知识底座规模 / LLM key / 轨迹存储
uv run tcms-agent doctor

# 看工具面与权限级别
uv run tcms-agent tools

# 跑一个目标（默认离线规则臂，无需任何 key）
uv run tcms-agent run "验证车门故障必须触发降级处置"

# 用真 LLM 决策（需 key，否则如实降级并提示）
uv run tcms-agent run "验证车门故障必须触发降级处置" --llm

# 回放某次运行的轨迹（逐超级步）
uv run tcms-agent history run-xxxxxxxxxxxx
```

---

## 这张图长什么样

```
START → plan → agent ─┬─(有工具调用 且 预算未超)→ act → agent   ← ReAct 循环
                      └─(收尾 / 预算耗尽)────────→ verify → report → END
```

- `plan`：目标 → 计划清单 + 锚定目标故障（规则解析，落在真实 `faults.yaml` 上）
- `agent`：**唯一的自由决策节点** —— LLM 决定调哪个工具，或收尾
- `act`：经**注册表**执行（权限门禁 + 审计 + 观察回填）
- `verify`：**引用强制校验** —— 结论引用的每个资产 id 回真实图谱核对
- `report`：汇总结构化 verdict

## 两个臂

| 臂 | 触发 | 用途 |
|---|---|---|
| `offline-rule` | 无 key / `--offline` | 确定性规则参考实现；走**同一张图同一套工具**，可复现、可进 CI |
| `llm` | 有 key 且 `--llm` | 真模型决策 |

结果里的 `model_kind` 如实标注，**不会把规则结果说成 AI 决策**。

## 工具权限

| 级别 | 现状 |
|---|---|
| **R0 只读**（7 个：kb_search / kb_filter_assets / symptom_diagnose / kb_node / list_scenarios / fault_detail / list_requirements） | ✅ |
| R1 沙箱写 / R2 真执行 / R3 持久化（需审批） | ⬜ 计划 M3 |

高权限工具**既不出现在送给模型的 schema 里，也不可在 invoke 时执行**（双保险）。

## 测试

```bash
cd packages/agent
pytest -q      # 37 passed：无 API key，全部离线可复现
```

覆盖：权限门禁（含"被拒的调用绝不能真的执行"）、诚实错误、审计记录、
引用校验（含"校验器不得误杀真实故障键"的自证）、端到端确定性、
预算强制、轨迹持久化与回放。

## 文档

- 架构：[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)
- 决策记录（含"为什么用 LangGraph、什么必须自研"）：[`docs/decisions.md`](../../docs/decisions.md)
