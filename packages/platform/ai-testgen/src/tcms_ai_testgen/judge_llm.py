"""LLM-as-judge（P4-C）：用 LLM 按规则 rubric 同口径打分，与 judge.py 交叉验证。

动机（章程 P4 / decisions D1）：规则 judge（judge.py）是确定性打分，但可能
漏判 LLM 生成的「语义错配」（如枚举信号断言用数字）——规则只能查关键词。
LLM judge 能读自然语言语义，但贵且不稳定。交叉验证回答：
    * 一致性：LLM judge 与规则 judge 在相同用例上的分差有多大？
    * LLM judge 是否捕捉到规则漏掉的缺陷（差异化价值）？

设计：
    - judge_llm(cases, client) -> LlmJudgeResult：让 LLM 逐条按 0-100 打分
      + 给一句理由（低分必给理由）；
    - 与 judge_quality 同输入同量纲，算 mean_abs_diff / spearman-ish 秩相关
      （n 小，用 Kendall tau 或直接列分差表，不硬上统计量）；
    - 离线不可测（需 LLM）→ 单测用 fake client；真实跑是实验（demo）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tcms_ai_testgen.judge import judge_quality
from tcms_ai_testgen.models import GeneratedCase

#: LLM judge 的系统 rubric（与 judge.py 同维度，注入 prompt）
_JUDGE_PROMPT = """你是测试用例质量评审专家。对每条测试用例按 0-100 打分，规则：
1. 结构完整度（30）：name/purpose/expected 齐备 15-30；缺关键字段 <15。
2. 需求可追溯（20）：covers 非空 +10~20；空 covers 记 0 并扣分。
3. 场景设计质量（30）：含边界/异常/时序/恢复关键词（边界、上限、越界、
   超时、断线、恢复、故障、拒绝）+10~30；模糊表述扣分。
4. 语义可执行性（20，规则 judge 没有的维度）：execution DSL 是否可能真实
   通过——枚举信号是否用文本断言、encode 值是否在物理范围、fault 键是否
   在已知集。明显会真实执行失败的用例 ≤10 分。

只输出 JSON：{{"scores": [{{"name": "<用例名>", "score": 0-100, "reason": "<一句话理由>"}}]}}
逐条对应输入顺序，不要遗漏。只输出 json fence 包裹的 JSON。"""


@dataclass
class CaseJudge:
    name: str
    rule_score: float
    llm_score: float
    llm_reason: str = ""
    diff: float = field(init=False)

    def __post_init__(self) -> None:
        self.diff = round(abs(self.llm_score - self.rule_score), 1)


@dataclass
class LlmJudgeResult:
    """一批用例的规则 vs LLM judge 交叉结果。"""

    cases: list[CaseJudge] = field(default_factory=list)
    raw_llm_output: str = ""

    @property
    def mean_abs_diff(self) -> float:
        if not self.cases:
            return 0.0
        return round(sum(c.diff for c in self.cases) / len(self.cases), 1)

    @property
    def agreement_rate(self) -> float:
        """|diff|<=15 视为一致（同档）的比例。"""
        if not self.cases:
            return 0.0
        agree = sum(1 for c in self.cases if c.diff <= 15)
        return round(agree / len(self.cases), 3)

    def as_dict(self) -> dict:
        return {
            "mean_abs_diff": self.mean_abs_diff,
            "agreement_rate": self.agreement_rate,
            "cases": [
                {"name": c.name, "rule": c.rule_score, "llm": c.llm_score,
                 "diff": c.diff, "reason": c.llm_reason}
                for c in self.cases
            ],
        }


def _parse_scores(raw: str) -> dict[str, tuple[float, str]]:
    """解析 LLM judge 输出 {"scores":[{name,score,reason}]} → {name: (score, reason)}。"""
    from tcms_ai_testgen.llm import extract_json

    payload = extract_json(raw)
    out: dict[str, tuple[float, str]] = {}
    if not isinstance(payload, dict):
        return out
    for item in payload.get("scores", []):
        if isinstance(item, dict) and item.get("name") is not None:
            try:
                out[str(item["name"])] = (float(item.get("score", 0)), str(item.get("reason", "")))
            except (TypeError, ValueError):
                continue
    return out


def judge_cases_with_llm(
    cases: list[GeneratedCase],
    client,
    *,
    max_cases: int = 12,
) -> LlmJudgeResult:
    """规则 judge 与 LLM judge 交叉打分。

    client 需支持 complete(prompt)（OpenAICompatClient）。max_cases 限制
    单次 LLM 调用规模（token 成本控制）。
    """
    selected = cases[:max_cases]
    rule_scores = {c.name: judge_quality([c]) for c in selected}
    if not hasattr(client, "complete"):
        raise RuntimeError("LLM judge 需要支持 complete(prompt) 的客户端")

    # 构造打分输入（每条：name + purpose + expected + execution 摘要）
    lines: list[str] = []
    for c in selected:
        ex = ""
        if c.execution:
            ex = f" execution={c.execution.model_dump_json()}"
        lines.append(f"- name={c.name} purpose={c.purpose} expected={c.expected}{ex}")
    prompt = _JUDGE_PROMPT + "\n\n待评用例：\n" + "\n".join(lines) + "\n"
    raw = client.complete(prompt, temperature=0.0)
    parsed = _parse_scores(raw)

    result = LlmJudgeResult(raw_llm_output=raw)
    for c in selected:
        llm_score, reason = parsed.get(c.name, (rule_scores.get(c.name, 0.0), "(未解析)"))
        result.cases.append(
            CaseJudge(name=c.name, rule_score=rule_scores.get(c.name, 0.0),
                      llm_score=llm_score, llm_reason=reason)
        )
    return result


__all__ = ["CaseJudge", "LlmJudgeResult", "judge_cases_with_llm"]
