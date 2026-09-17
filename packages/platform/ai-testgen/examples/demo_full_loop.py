"""演示/自检：一条命令跑通「需求 → 生成 → 编译 → 真实执行 → 量化报告」。

用法（需上游 tcms-can-test 可用；TCMS_UPSTREAM_ROOT 可覆盖路径）：
    python examples/demo_full_loop.py --source mock_llm --num 40 --out docs/reports/demo.json
    python examples/demo_full_loop.py --source rule_baseline
    python examples/demo_full_loop.py --mutation     # 附变异杀毒
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tcms_ai_testgen.asset_loader import default_upstream_root
from tcms_ai_testgen.baseline_rule import RuleBaselineClient
from tcms_ai_testgen.executor_real import run_real
from tcms_ai_testgen.llm import MockLLMClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.mutation import list_mutations, run_mutation


def main() -> int:
    p = argparse.ArgumentParser(description="tcms-ai-testgen 全链路 demo")
    p.add_argument("--source", choices=["mock_llm", "rule_baseline"], default="mock_llm")
    p.add_argument("--num", type=int, default=40, help="生成用例数（mock 建议 ≥36 以覆盖 10 故障键 + 边界/车门族）")
    p.add_argument("--mutation", action="store_true", help="附变异杀毒实验")
    p.add_argument("--out", default=None, help="报告 JSON 输出路径")
    args = p.parse_args()

    root = default_upstream_root()
    if not (root / "tests" / "conftest.py").is_file():
        print(f"[!] 上游仓库不可用: {root}（设置 TCMS_UPSTREAM_ROOT）")
        return 1

    req = GenRequest(target="TCMS 信号/故障（真实资产驱动）",
                     requirements=["边界与越界保护", "车门故障联锁", "超速降级处置"],
                     num_cases=args.num)
    client = RuleBaselineClient() if args.source == "rule_baseline" else MockLLMClient()
    raw = client.generate_cases(req)
    cases, failures = parse_cases(extract_json(raw))

    t0 = time.time()
    res = run_real(cases, root)
    exec_s = round(time.time() - t0, 2)

    mutation_rows: list[dict] = []
    if args.mutation:
        for m in list_mutations():
            mr = run_mutation(cases, m, root, baseline=res)
            mutation_rows.append(mr.as_dict())

    report = {
        "source": args.source,
        "requested": args.num,
        "parsed": len(cases),
        "failures": failures,
        "parse_rate": round(len(cases) / max(1, len(cases) + failures), 3),
        "compile_rate": res.compile_rate,
        "exec_pass_rate": res.exec_pass_rate,
        "real_exec_seconds": exec_s,
        "mutation": mutation_rows,
        "upstream": str(root),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[report] 已写入 {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
