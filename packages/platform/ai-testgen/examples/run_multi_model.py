"""多模型 × 多批正式对比实验（补 README roadmap 首个 [ ]）。

用法：
    python examples/run_multi_model.py --models deepseek-v4-flash,deepseek-v3.2,deepseek-r1 \
        --batches 3 --num 8 [--mutation] [--out docs/reports/multi_model.json]

与 mock/rule_baseline 同契约同管线：prompt v2 强制 execution DSL 输出，
产物走 parse → compile → 真实 pytest → 变异杀毒。结果回答：
    1. 多模型（v4-flash / v3.2 / r1）在同一管线上的质量差异是否可量化；
    2. 模型幻觉（非法信号值等）被真实执行器拦截的比例；
    3. 与 mock/规则基线对照「LLM 增量」的稳定性（批间方差）。

指标口径（docs/metrics.md）：parse_rate / compile_rate / exec_pass_rate
（真实 pytest）+ 变异杀毒 kill_rate（相关用例分母）。
诚实边界：单模型单温度、固定 seed；r1 为推理模型可能输出格式漂移，
如实计入 failures/parse_rate 而非隐藏。

默认端点 = 阿里云百炼兼容模式；key 从 DSH .credentials.yaml 读取（ALIYUN_API_KEY）
或环境变量 DASH_API_KEY / LLM_API_KEY / DEEPSEEK_API_KEY。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets
from tcms_ai_testgen.asset_prompt import build_prompt_context
from tcms_ai_testgen.executor_real import run_real
from tcms_ai_testgen.llm import OpenAICompatClient, extract_json, parse_cases
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.mutation import list_mutations, run_mutation

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODELS = ["deepseek-v4-flash", "deepseek-v3.2", "deepseek-r1"]


def _load_api_key() -> str:
    """读 key：环境变量优先，其次 DSH .credentials.yaml（ALIYUN_API_KEY）。"""
    for env in ("LLM_API_KEY", "DASH_API_KEY", "DEEPSEEK_API_KEY"):
        v = os.environ.get(env)
        if v:
            return v
    try:
        import yaml

        cred_path = Path.home() / ".dsh" / ".credentials.yaml"
        cred = yaml.safe_load(cred_path.read_text(encoding="utf-8"))
        v = cred.get("refs", {}).get("ALIYUN_API_KEY")
        if v:
            return str(v)
    except Exception:
        pass
    raise SystemExit("[!] 需要 API key：设置 DASH_API_KEY / LLM_API_KEY / 或 .credentials.yaml ALIYUN_API_KEY")


def _request(num: int) -> GenRequest:
    return GenRequest(
        target="TCMS 信号边界/车门故障/超速处置（真实 DBC 资产驱动）",
        requirements=["越界值编码拒绝", "车门故障检出为 Fault", "超速触发降级处置", "心跳丢失检测"],
        hints=["expect_signal 枚举用文本（如 'Fault'）", "expect_encode_error 用物理越界值"],
        num_cases=num,
        tier="safety",
    )


def _run_batch(
    model: str,
    api_key: str,
    base_url: str,
    ctx: str,
    req: GenRequest,
    root: Path,
    with_mutation: bool,
) -> dict:
    """单模型单批全链路：生成 → 真实执行 → （可选）变异杀毒。

    单批失败（网络超时/限流/解析全败）不抛异常——返回 error 标记，
    由 main 决定重试或跳过，保证多模型实验整体可继续。
    """
    client = OpenAICompatClient(
        model=model,
        base_url=base_url,
        api_key=api_key,
        asset_context=ctx,
        temperature=0.3,
        timeout=300.0,  # r1 等推理模型生成慢，超时要放宽
    )
    t0 = time.time()
    cases: list = []
    fails = 0
    raw = None
    last_err = ""
    for attempt in range(3):  # 网络抖动/推理慢：最多重试 3 次
        try:
            raw = client.generate_cases(req)
            break
        except Exception as exc:  # 超时/限流/连接错——重试
            last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"    [attempt {attempt + 1} 失败: {last_err}]", flush=True)
            time.sleep(5 * (attempt + 1))
    if raw is None:
        return {"model": model, "error": f"3 次调用均失败: {last_err}"}
    payload = extract_json(raw)
    cases, fails = parse_cases(payload)
    # LLM 输出偶发非 JSON（格式漂移/截断）：重试保批有效（与 run_llm_arm 同策略）
    for attempt in range(2):
        if fails > 0 and len(cases) < max(1, req.num_cases // 2):
            raw = client.generate_cases(req)
            payload = extract_json(raw)
            cases, fails = parse_cases(payload)
        else:
            break
    gen_s = round(time.time() - t0, 2)

    res = run_real(cases, root)
    exec_s = round(time.time() - t0 - gen_s, 2)

    hallucinations = []
    if res.failed:
        for ln in res.stdout.splitlines():
            if "FAILED" in ln or "EncodeError" in ln:
                hallucinations.append(ln.strip()[:160])

    mut_rows: list[dict] = []
    if with_mutation and cases:
        for m in list_mutations():
            try:
                mr = run_mutation(cases, m, root, baseline=res)
                mut_rows.append(mr.as_dict())
            except Exception as exc:  # 变异执行失败不拖垮整批（诚实记录）
                mut_rows.append({"mutation": m, "error": str(exc)[:120]})

    return {
        "model": model,
        "parsed": len(cases),
        "failures": fails,
        "parse_rate": round(len(cases) / max(1, len(cases) + fails), 3),
        "compile_rate": res.compile_rate,
        "exec_pass_rate": res.exec_pass_rate,
        "generation_seconds": gen_s,
        "real_exec_seconds": exec_s,
        "hallucination_caught": hallucinations,
        "mutation": mut_rows,
    }


def _aggregate(batches: list[dict], model: str) -> dict:
    """跨批聚合：中位数/均值 + 批间极差（稳定性信号）。error 批跳过并在结果标注。"""
    ok = [b for b in batches if "error" not in b]
    errors = [b["error"] for b in batches if "error" in b]
    agg: dict = {"model": model, "batches": len(batches)}
    if errors:
        agg["batch_errors"] = errors
    if not ok:
        agg["note"] = "全部批次失败（网络/API），无有效指标"
        return agg
    rates = {"parse_rate": [], "compile_rate": [], "exec_pass_rate": []}
    for b in ok:
        for k in rates:
            rates[k].append(b[k])
    for k, vals in rates.items():
        agg[k] = {
            "median": round(statistics.median(vals), 3),
            "mean": round(statistics.mean(vals), 3),
            "min": round(min(vals), 3),
            "max": round(max(vals), 3),
            "spread": round(max(vals) - min(vals), 3),  # 批间波动
            "per_batch": vals,
        }
    # 变异杀毒跨批合并（同 mutation 名聚合）
    mut_by_name: dict[str, list[dict]] = {}
    for b in ok:
        for m in b.get("mutation", []):
            mut_by_name.setdefault(m.get("mutation", "?"), []).append(m)
    agg["mutation"] = {}
    for name, rows in mut_by_name.items():
        if any("error" in r for r in rows):
            agg["mutation"][name] = {"error": "某批变异执行失败，见批次明细"}
            continue
        total_rel = sum(r["relevant"] for r in rows)
        total_killed = sum(r["killed"] for r in rows)
        agg["mutation"][name] = {
            "relevant_total": total_rel,
            "killed_total": total_killed,
            "kill_rate_pooled": round(total_killed / total_rel, 3) if total_rel else None,
        }
    # 幻觉聚合
    hall_total = sum(len(b.get("hallucination_caught", [])) for b in ok)
    agg["hallucination_total"] = hall_total
    agg["hallucination_samples"] = [
        h for b in ok for h in b.get("hallucination_caught", [])
    ][:5]
    return agg


def main() -> int:
    p = argparse.ArgumentParser(description="多模型 × 多批正式对比实验")
    p.add_argument("--models", default=",".join(DEFAULT_MODELS))
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--batches", type=int, default=3)
    p.add_argument("--num", type=int, default=8)
    p.add_argument("--mutation", action="store_true", help="每批跑变异杀毒（耗时约 3x）")
    p.add_argument("--out", default=None)
    p.add_argument("--sleep", type=float, default=2.0, help="批间 sleep 秒（防限流）")
    args = p.parse_args()

    api_key = _load_api_key()
    root = default_upstream_root()
    if not (root / "tests" / "conftest.py").is_file():
        print(f"[!] 上游仓库不可用: {root}（设置 TCMS_UPSTREAM_ROOT）")
        return 1
    bundle = load_assets(root / "tcms", root / "scenarios")
    ctx = build_prompt_context(bundle)
    print(f"[multi-model] root={root}\n[multi-model] key 来源=env/.credentials\n")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    report: dict = {
        "note": "多模型 × 多批正式对比（deepseek 家族 @ 百炼，temperature 0.3，固定请求模板）",
        "batches_per_model": args.batches,
        "num_per_batch": args.num,
        "mutation": args.mutation,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": {},
    }
    for model in models:
        print(f"===== {model} × {args.batches} 批 =====", flush=True)
        batches = []
        for bi in range(1, args.batches + 1):
            print(f"--- batch {bi}/{args.batches} ---", flush=True)
            b = _run_batch(model, api_key, args.base_url, ctx, _request(args.num), root, args.mutation)
            if "error" in b:
                print(f"    [batch {bi} 失败: {b['error']}]（跳过继续）", flush=True)
            else:
                print(f"    parsed={b['parsed']} fails={b['failures']} "
                      f"compile={b['compile_rate']} exec={b['exec_pass_rate']}"
                      + (f"  幻觉={len(b['hallucination_caught'])}" if b["hallucination_caught"] else ""),
                      flush=True)
            batches.append(b)
            if bi < args.batches:
                time.sleep(args.sleep)
        report["results"][model] = _aggregate(batches, model)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print("\n" + "=" * 70)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n[report] 已写入 {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
