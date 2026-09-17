"""编排器：把「生成 -> 解析 -> 执行 -> 打分」串成一条可复现流水线。

对外主入口 ``run_pipeline(req, client=None) -> GenReport``：
1. LLM（默认 mock）生成原始文本；
2. 结构化解析（失败计数）；
3. 确定性执行器跑通过率；
4. judge 打质量分。

流水线本身不抛业务异常：解析失败的用例进 failures 统计，
整批结果永远返回一个可量化的 GenReport——「质量可度量」是本项目核心。
"""

from __future__ import annotations

from tcms_ai_testgen.executor import run_deterministic
from tcms_ai_testgen.judge import judge_quality
from tcms_ai_testgen.llm import LLMClient, MockLLMClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenReport, GenRequest


def run_pipeline(req: GenRequest, client: LLMClient | None = None) -> GenReport:
    """跑完整流水线，返回量化报告。client 缺省为离线 mock。"""
    client = client or MockLLMClient()
    raw = client.generate_cases(req)
    # LLM 输出是文本：先抽 JSON，再结构化解析；抽不出整体计 1 次失败
    payload = raw if isinstance(raw, dict) else extract_json(raw)
    cases, failures = parse_cases(payload)
    exec_result = run_deterministic(cases)
    quality = judge_quality(cases)
    report = GenReport(
        request=req,
        cases=cases,
        failures=failures,
        quality_score=quality,
        exec_pass_rate=exec_result.exec_pass_rate,
        coverage_gain=None,  # 二期：接入真实覆盖率后填
    )
    return report


def demo_default() -> GenReport:
    """默认演示场景：TCMS 紧急制动 EBM（mock 模式，离线可跑）。"""
    req = GenRequest(
        target="列车 TCMS 紧急制动管理（EBM）",
        requirements=[
            "超速时触发紧急制动",
            "断线时自动告警",
            "故障恢复后可缓解",
        ],
        hints=["覆盖速度边界 0/限速值/限速+1", "断线发生在制动缓解窗口"],
        num_cases=8,
        tier="safety",
    )
    return run_pipeline(req)


if __name__ == "__main__":  # pragma: no cover
    report = demo_default()
    print("target:", report.request.target)
    print("summary:", report.summary())
