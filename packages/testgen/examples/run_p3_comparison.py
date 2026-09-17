"""P3 多生成源对比实验（可复现脚本）。

用法：
    python examples/run_p3_comparison.py          # 需上游仓库存在
    TCMS_UPSTREAM_ROOT=D:/x/tcms-can-test python examples/run_p3_comparison.py

对照臂（同 DSL、同真实执行管线）：
    A rule_baseline : 机械规则（仅 encode_bound，无场景/故障语义）
    B mock_llm      : DSL 一致性套件（encode + simulate + fault 全族，oracle 派生）

指标（docs/metrics.md）：
    parse_rate / compile_rate / exec_pass_rate（真实执行）
    + 变异杀毒：对 3 个行为翻转求 kill_rate（相关用例分母）

预期结论（供实验报告验证）：
    - 两臂 compile/exec 都可能高（encode_bound 简单）；
    - 但变异覆盖上 A 远弱于 B：A 对 M1/M3 relevant≈0 → 杀毒力不足，
      说明 A 的用例「测不到」车门/故障语义；
    - 量化区分度 = 按变异加权的总杀毒率（B >> A）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tcms_ai_testgen.asset_loader import default_upstream_root
from tcms_ai_testgen.baseline_rule import RuleBaselineClient
from tcms_ai_testgen.executor_real import run_real
from tcms_ai_testgen.llm import MockLLMClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.mutation import list_mutations, run_mutation


def _gen_cases(client, req: GenRequest, label: str) -> list:
    raw = client.generate_cases(req) if not isinstance(client, RuleBaselineClient) else client.generate_cases(req)
    payload = extract_json(raw) if isinstance(raw, str) else raw
    cases, fails = parse_cases(payload)
    return cases, fails


def run_comparison(upstream_root: str | Path, num_cases: int = 20) -> dict:
    root = Path(upstream_root)
    req = GenRequest(target="TCMS 信号/故障", requirements=["边界", "故障"], num_cases=num_cases)
    arms: dict[str, dict] = {}

    sources = {"rule_baseline": RuleBaselineClient(), "mock_llm": MockLLMClient()}
    for label, client in sources.items():
        raw = client.generate_cases(req)
        payload = extract_json(raw)
        cases, fails = parse_cases(payload)
        # 真实执行 baseline
        res = run_real(cases, root)
        # 变异杀毒
        mut_rows = []
        for m in list_mutations():
            mr = run_mutation(cases, m, root, baseline=res)
            mut_rows.append(mr.as_dict())
        arms[label] = {
            "parsed": len(cases),
            "failures": fails,
            "parse_rate": round(len(cases) / max(1, len(cases) + fails), 3),
            "compile_rate": res.compile_rate,
            "exec_pass_rate": res.exec_pass_rate,
            "mutation": mut_rows,
            # 加权总杀毒率（按 relevant 加权）
            "weighted_kill_rate": round(
                sum(mr["kill_rate"] * mr["relevant"] for mr in mut_rows)
                / max(1, sum(mr["relevant"] for mr in mut_rows)),
                3,
            ),
            "mutant_coverage": sum(1 for mr in mut_rows if mr["relevant"] > 0),
        }
    return {"sources": arms, "num_cases": num_cases}


if __name__ == "__main__":  # pragma: no cover
    root = default_upstream_root()
    if not (root / "tests" / "conftest.py").is_file():
        print(f"[!] 上游仓库不可用: {root}")
        sys.exit(1)
    report = run_comparison(root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
