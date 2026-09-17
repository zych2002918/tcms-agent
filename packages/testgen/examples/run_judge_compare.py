"""P4-C demo：规则 judge vs LLM judge 交叉验证。

用法：
    set DASH_API_KEY=sk-xxx
    python examples/run_judge_compare.py --source mock_llm --num 8 [--out report.json]

流程：生成用例 → judge_quality（规则）与 LLM judge（同 rubric + 语义可执行
维度）分别打分 → mean_abs_diff / agreement_rate 交叉结论。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets
from tcms_ai_testgen.asset_prompt import build_prompt_context
from tcms_ai_testgen.judge import judge_quality
from tcms_ai_testgen.judge_llm import judge_cases_with_llm
from tcms_ai_testgen.llm import MockLLMClient, OpenAICompatClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def main() -> int:
    p = argparse.ArgumentParser(description="规则 judge vs LLM judge 交叉验证")
    p.add_argument("--source", choices=["mock_llm", "llm"], default="mock_llm")
    p.add_argument("--model", default=os.environ.get("LLM_MODEL", "deepseek-v3.2"))
    p.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", DEFAULT_BASE))
    p.add_argument("--num", type=int, default=8)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("DASH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[!] 需要 API key：DASH_API_KEY / LLM_API_KEY / DEEPSEEK_API_KEY")
        return 1

    root = default_upstream_root()
    bundle = load_assets(root / "tcms", root / "scenarios")
    ctx = build_prompt_context(bundle)
    judge_client = OpenAICompatClient(model=args.model, base_url=args.base_url,
                                      api_key=api_key, asset_context=ctx)

    req = GenRequest(target="TCMS 报警/信号边界/车门", requirements=["报警合法", "车速越界", "车门故障"],
                     num_cases=args.num)
    if args.source == "llm":
        gen_client = OpenAICompatClient(model=args.model, base_url=args.base_url,
                                        api_key=api_key, asset_context=ctx)
        raw = gen_client.generate_cases(req)
    else:
        raw = MockLLMClient().generate_cases(req)
    cases, fails = parse_cases(extract_json(raw))

    rule = judge_quality(cases)
    llm_res = judge_cases_with_llm(cases, judge_client)
    report = {
        "demo": "p4c-judge-crosscheck",
        "source": args.source,
        "model": args.model,
        "parsed": len(cases),
        "rule_quality": rule,
        "judge": llm_res.as_dict(),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n[report] 已写入 {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
