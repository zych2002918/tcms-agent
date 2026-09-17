"""规则基线生成源（P3 对照臂 A）—— 机械规则，无 oracle/无场景理解。

用途（docs/experiments/p3-source-comparison.md）：
    与 MockLLM（oracle 派生、场景组合）做同 DSL 同管线的对照，证明量化
    管线能区分「生成源质量」。规则基线代表「不会写测试的人/纯查表」：
    - 只从 DBC 物理范围机械推导「边界+1 应越界」用例；
    - 不做场景关联、不做故障语义、期望不查 oracle（硬编码动作词）；
    - 产物仍是合法 execution DSL——所以差异可归因于「生成策略」而非 DSL。

它故意比 MockLLM「弱」在哪：
    1. 断言面窄（只有 encode_bound）；
    2. 期望可能编造（如对无枚举的信号断言错误枚举文本）；
    3. 无故障/时序组合（fault_scenario / simulate 全缺）。
预期：compile_rate 高（模板合法）但 kill_rate / 真实通过率低于 MockLLM，
或者 parse 出「语义错配」用例。这就是可量化的「质量差」。
"""

from __future__ import annotations

from typing import Any


class RuleBaselineClient:
    """规则基线：按 DBC 物理范围机械生成 encode_bound 用例（无场景理解）。"""

    name = "rule_baseline"

    def generate_cases(self, req) -> str:  # 与 LLMClient.generate_cases 同签名
        import json

        cases: list[dict[str, Any]] = []
        # 内置信号物理边界（镜像自 tcms.dbc [min|max]，机械推导）
        bounds = [
            ("VehicleSpeed", "SpeedKmh", 200.0),
            ("TractionBrakeHandle", "HandlePosition", 16),
            ("EnergyStatus", "SocPercent", 100),
            ("EnergyStatus", "BatteryTemp", 120),  # 上限 -40..120
            ("PantographStatus", "LineVoltage", 30000),
        ]
        for i, (msg, sig, hi) in enumerate(bounds, start=1):
            over = hi + (1.0 if isinstance(hi, float) else 1)
            # 机械：越界一条 + 上限一条
            cases.append(
                {
                    "name": f"test_rule_bound_over_{msg}_{sig}",
                    "purpose": f"{msg}.{sig} 超过物理上限 {hi} 应拒绝",
                    "preconditions": "DBC 就绪",
                    "steps": ["构造越界值", "编码"],
                    "expected": "编码应拒绝（机械规则）",
                    "covers": [],
                    "tier": "smoke",
                    "execution": {
                        "kind": "encode_bound",
                        "setup": [],
                        "expect": [
                            {"op": "expect_encode_error", "args": {"message": msg, "signal": sig, "value": over}}
                        ],
                    },
                }
            )
            cases.append(
                {
                    "name": f"test_rule_bound_ok_{msg}_{sig}",
                    "purpose": f"{msg}.{sig} 等于物理上限 {hi} 应可编码",
                    "preconditions": "DBC 就绪",
                    "steps": ["构造上限值", "编码"],
                    "expected": "编码成功（机械规则）",
                    "covers": [],
                    "tier": "smoke",
                    "execution": {
                        "kind": "encode_bound",
                        "setup": [],
                        "expect": [
                            {"op": "expect_encode_ok", "args": {"message": msg, "signal": sig, "value": hi}}
                        ],
                    },
                }
            )
        return json.dumps({"cases": cases}, ensure_ascii=False)


# 便于实验脚本统一接口
def build_baseline() -> RuleBaselineClient:
    return RuleBaselineClient()
