"""P4 反思闭环 demo：真 LLM 生成 → 真实执行 → 失败自动修正 → 自愈报告。

用法：
    set DASH_API_KEY=sk-xxx
    python examples/run_reflect_demo.py --model deepseek-v3.2 --num 8 [--out report.json]

流程（docs/experiments/p4-agent.md）：
    round1: LLM 生成 execution DSL → 真实 pytest 执行
    fix:    失败用例 → 分类(class1/class2/class3) → LLMReflector
            （失败详情 + oracle 信号域值 + RAG 上游金标证据）→ 修正
    round2: 修正版替换失败项全量重跑 → 逐例状态机 + self_heal_rate
指标：self_heal_rate = healed / (healed + not_progressed)
（成本：2 轮 LLM 调用 + 2 轮真实执行；mock 闭环验证见 tests/test_agent.py）
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from tcms_ai_testgen.agent import LLMReflector, reflect_loop
from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets
from tcms_ai_testgen.asset_prompt import build_prompt_context
from tcms_ai_testgen.executor_real import run_real
from tcms_ai_testgen.llm import OpenAICompatClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.rag import GoldenIndex

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def main() -> int:
    p = argparse.ArgumentParser(description="P4 反思闭环 demo（真 LLM）")
    p.add_argument("--model", default=os.environ.get("LLM_MODEL", "deepseek-v3.2"))
    p.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", DEFAULT_BASE))
    p.add_argument("--num", type=int, default=8)
    p.add_argument("--seed-note", default="", help="随机性备注（如批次号）")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("DASH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        try:  # 回退：DSH .credentials.yaml（ALIYUN_API_KEY）
            import yaml as _yaml

            _cred_path = Path.home() / ".dsh" / ".credentials.yaml"
            _cred = _yaml.safe_load(_cred_path.read_text(encoding="utf-8"))
            api_key = str(_cred.get("refs", {}).get("ALIYUN_API_KEY") or "")
        except Exception:
            api_key = ""
    if not api_key:
        print("[!] 需要 API key：DASH_API_KEY / LLM_API_KEY / 或 .credentials.yaml ALIYUN_API_KEY")
        return 1

    root = default_upstream_root()
    bundle = load_assets(root / "tcms", root / "scenarios")
    ctx = build_prompt_context(bundle)
    client = OpenAICompatClient(model=args.model, base_url=args.base_url, api_key=api_key,
                                asset_context=ctx)
    rag = GoldenIndex.from_tests_dir(root / "tests")

    req = GenRequest(
        target="TCMS 报警/信号边界/车门（反思闭环 demo）",
        requirements=["报警编码合法域值", "车速越界拒绝", "车门故障检出", "心跳丢失"],
        num_cases=args.num,
    )
    t0 = time.time()
    # LLM 输出偶发非 JSON（格式漂移/截断）：最多重试 2 次保证 demo 稳定
    raw = client.generate_cases(req)
    cases, fails = parse_cases(extract_json(raw))
    for attempt in range(2):
        if fails > 0 and len(cases) < max(1, args.num // 2):
            print(f"[retry {attempt + 1}] LLM 输出解析不足（{len(cases)}/{args.num}），重新生成 ...")
            raw = client.generate_cases(req)
            cases, fails = parse_cases(extract_json(raw))
        else:
            break
    r1 = run_real(cases, root)
    ref = LLMReflector(client, rag_index=rag)
    rep = reflect_loop(cases, root, lambda c, i, s: ref.fix(c, i, s))

    report = {
        "demo": "p4-reflect-loop",
        "model": args.model,
        "note": args.seed_note or "LLM 随机输出，单批结果不宣称统计意义",
        "generated": {"parsed": len(cases), "failures": fails},
        "round1": {"passed": r1.passed, "failed": r1.failed},
        "reflect": rep.as_dict(),
        "elapsed_seconds": round(time.time() - t0, 1),
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
