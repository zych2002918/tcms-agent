"""CLI：命令行跑一次生成流水线（默认 mock 离线；--llm 走真实模型）。

用法示例：
    python -m tcms_ai_testgen.cli                    # mock 演示（EBM 场景）
    python -m tcms_ai_testgen.cli --num 10            # 自定义数量
    python -m tcms_ai_testgen.cli --target "ATP 超速防护"
    # 真实 LLM（需 pip install .[llm] 且配置环境变量）
    DEEPSEEK_API_KEY=sk-xxx python -m tcms_ai_testgen.cli --llm
"""

from __future__ import annotations

import argparse
import json
import os

from tcms_ai_testgen.llm import build_client
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.pipeline import run_pipeline


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM 测试用例生成器（TCMS 演示）")
    p.add_argument("--target", default="列车 TCMS 紧急制动管理（EBM）")
    p.add_argument("--num", type=int, default=8, help="生成用例数")
    p.add_argument("--tier", choices=["smoke", "safety", "regression"], default="safety")
    p.add_argument("--req", nargs="+", default=["超速时触发紧急制动", "断线时自动告警"], help="需求清单")
    p.add_argument("--hint", nargs="+", default=["覆盖速度边界 0/限速值/限速+1"])
    p.add_argument("--llm", action="store_true", help="使用真实 LLM（默认 mock 离线）")
    p.add_argument("--json", action="store_true", help="只输出 JSON 摘要")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    req = GenRequest(
        target=args.target,
        requirements=list(args.req),
        hints=list(args.hint),
        num_cases=args.num,
        tier=args.tier,
    )
    client = build_client(
        mock=not args.llm,
        api_key=os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("OPENAI_BASE_URL"),
    )
    report = run_pipeline(req, client)
    if args.json:
        print(json.dumps(report.summary(), ensure_ascii=False, indent=2))
    else:
        print(f"target     : {req.target}")
        print(f"requested  : {req.num_cases}")
        print(f"parsed     : {len(report.cases)}   failures: {report.failures}")
        print(f"parse_rate : {report.parse_rate:.1%}")
        print(f"quality    : {report.quality_score}/100")
        print(f"exec_pass  : {report.exec_pass_rate:.1%}")
        for c in report.cases:
            print(f"  - {c.name}  [{c.tier}] {c.purpose[:40]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
