"""真 LLM 臂实验（P3-LLM）：百炼/DeepSeek 兼容端点 × 同一 DSL 管线。

用法：
    set DASH_API_KEY=sk-xxx          # 阿里云百炼 key（sk-ws- 开头）
    python examples/run_llm_arm.py --model deepseek-v3.2 --num 8 [--mutation] [--out report.json]

与 mock/rule_baseline 同契约同管线：prompt v2 强制 execution DSL 输出，
产物走 parse → compile → 真实 pytest → 变异杀毒。结果用于：
    1. 验证「真 LLM 能产出可编译 execution」（契约有效性）；
    2. 暴露模型幻觉（如非法信号值）被真实执行器拦截的证据；
    3. 与 mock/规则基线对照「LLM 增量」。

默认端点 = 阿里云百炼兼容模式（DASHSCOPE 兼容 OpenAI）；可用环境变量覆盖：
    LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets
from tcms_ai_testgen.asset_prompt import build_prompt_context
from tcms_ai_testgen.executor_real import run_real
from tcms_ai_testgen.llm import OpenAICompatClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.mutation import list_mutations, run_mutation

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "deepseek-v3.2"


def main() -> int:
    p = argparse.ArgumentParser(description="真 LLM 臂实验（百炼/DeepSeek 兼容）")
    p.add_argument("--model", default=os.environ.get("LLM_MODEL", DEFAULT_MODEL))
    p.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", DEFAULT_BASE))
    p.add_argument("--num", type=int, default=8, help="请求生成的用例数")
    p.add_argument("--mutation", action="store_true", help="跑变异杀毒")
    p.add_argument("--out", default=None, help="报告 JSON 路径")
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
        print("[!] 需要 API key：DASH_API_KEY / LLM_API_KEY / DEEPSEEK_API_KEY")
        return 1

    root = default_upstream_root()
    bundle = load_assets(root / "tcms", root / "scenarios")
    ctx = build_prompt_context(bundle)

    client = OpenAICompatClient(
        model=args.model,
        base_url=args.base_url,
        api_key=api_key,
        asset_context=ctx,
    )
    req = GenRequest(
        target="TCMS 信号边界/车门故障/超速处置（真实 DBC 资产驱动）",
        requirements=["越界值编码拒绝", "车门故障检出为 Fault", "超速触发降级处置", "心跳丢失检测"],
        hints=["expect_signal 枚举用文本（如 'Fault'）", "expect_encode_error 用物理越界值"],
        num_cases=args.num,
        tier="safety",
    )

    print(f"[llm] model={args.model} 请求 {args.num} 条 ...")
    t0 = time.time()
    raw = client.generate_cases(req)
    payload = extract_json(raw)
    cases, fails = parse_cases(payload)
    # LLM 输出偶发非 JSON（格式漂移/截断）：重试保 demo 稳定
    for attempt in range(2):
        if fails > 0 and len(cases) < max(1, args.num // 2):
            print(f"[retry {attempt + 1}] LLM 输出解析不足（{len(cases)}/{args.num}），重新生成 ...")
            raw = client.generate_cases(req)
            payload = extract_json(raw)
            cases, fails = parse_cases(payload)
        else:
            break
    gen_s = round(time.time() - t0, 2)
    res = run_real(cases, root)
    exec_s = round(time.time() - t0 - gen_s, 2)

    mut_rows: list[dict] = []
    if args.mutation:
        for m in list_mutations():
            mr = run_mutation(cases, m, root, baseline=res)
            mut_rows.append(mr.as_dict())

    # 幻觉案例（真实执行失败 = 模型输出被真实平台拦截）
    hallucinations = []
    if res.failed:
        for ln in res.stdout.splitlines():
            if "FAILED" in ln or "EncodeError" in ln:
                hallucinations.append(ln.strip()[:200])

    report = {
        "source": f"llm:{args.model}",
        "requested": args.num,
        "generation_seconds": gen_s,
        "real_exec_seconds": exec_s,
        "parsed": len(cases),
        "failures": fails,
        "parse_rate": round(len(cases) / max(1, len(cases) + fails), 3),
        "compile_rate": res.compile_rate,
        "exec_pass_rate": res.exec_pass_rate,
        "hallucination_caught": hallucinations,
        "mutation": mut_rows,
        "note": "真 LLM 数字仅代表该模型该次输出（temperature 0.3）；与 mock/规则基线对照见 p3 报告",
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
