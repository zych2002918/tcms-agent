"""LLM-as-judge 质量评估：对生成用例打分（0-100）。

一期用确定性规则评分（可离线、可复现），规则即「测试专家 rubric」：
- 结构完整度（必填字段是否齐全）
- 需求可追溯度（covers 命中率）
- 场景设计质量（是否含边界/异常/时序关键词）

二期可替换为真 LLM judge（同一接口），把 rubric 注入 prompt。
"""

from __future__ import annotations

from tcms_ai_testgen.models import GeneratedCase

#: 场景设计加分关键词
_BOUNDARY_WORDS = ("边界", "上限", "下限", "最大", "最小", "超时", "断线", "峰值", "异常", "重复")
_TRACEABILITY_PENALTY = 20  # covers 为空时扣分


def judge_quality(cases: list[GeneratedCase]) -> float:
    """返回 0-100 质量分（按用例平均）。空输入返回 0。"""
    if not cases:
        return 0.0
    total = 0.0
    for case in cases:
        score = 60.0  # 基础分：能解析出来就及格线起步
        # 1) 结构完整度：必填齐全 +20
        if case.name and case.purpose and case.expected:
            score += 15
        if case.preconditions and case.steps:
            score += 5
        # 2) 需求可追溯度
        if case.covers:
            score += 10
        else:
            score -= _TRACEABILITY_PENALTY
        # 3) 场景设计质量：出现边界/异常词 +10（最多一次）
        if any(w in (case.purpose + case.name) for w in _BOUNDARY_WORDS):
            score += 10
        total += max(0.0, min(100.0, score))
    return round(total / len(cases), 1)
