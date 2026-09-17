"""一键全流程演示（demo_tour）：五幕串起 tcms-ai-testgen 全部能力。

用法：
    python examples/demo_tour.py                     # 离线五幕（推荐先跑这个）
    set DASH_API_KEY=sk-xxx
    python examples/demo_tour.py --llm              # 含真 LLM 幕（第 5 幕反思闭环）
    python examples/demo_tour.py --llm --llm-model deepseek-v3.2

五幕结构（每幕独立可跑，输出关键指标）：
    幕1 资产加载   真实 tcms.dbc(22报文/116信号) + 104 场景 + 636 金标索引
    幕2 生成+编译   mock 生成 → execution DSL 编译率
    幕3 真实执行   生成用例 → 上游真实 pytest → exec_pass_rate
    幕4 变异杀毒   3 个行为翻转 → kill_rate（质量标尺）
    幕5 反思闭环   [需 key] 真 LLM 失败自愈 self_heal_rate（或提示跳过）

讲解话术见 docs/interview_guide.md 第 三 节；离线幕不联网、CI 可跑。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EX = Path(__file__).resolve().parent


def _setup_utf8() -> None:
    """强制 stdout/stderr UTF-8（Windows 终端 GBK 会乱码——演示现场关键）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _run(label: str, cmd: list[str], timeout: int = 300) -> int:
    print(f"\n{'=' * 62}\n▶ {label}\n{'=' * 62}")
    # 子进程同样强制 UTF-8 输出，避免 capture 后乱码
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)
    out = (proc.stdout or "").strip()
    # 打印全部 stdout（demo 输出即讲解素材），stderr 仅失败时显示
    print(out if out else "(无输出)")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if err:
            print(f"[stderr 尾部] {err[-600:]}")
        print(f"[!] {label} 退出码 {proc.returncode}")
    return proc.returncode


def main() -> int:
    p = argparse.ArgumentParser(description="tcms-ai-testgen 一键全流程演示")
    p.add_argument("--llm", action="store_true", help="含真 LLM 幕（需 DASH_API_KEY）")
    p.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "deepseek-v3.2"))
    p.add_argument("--num", type=int, default=24, help="mock 生成用例数（幕2-4）")
    args = p.parse_args()

    py = sys.executable
    key = os.environ.get("LLM_API_KEY") or os.environ.get("DASH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    has_key = args.llm and bool(key)

    _setup_utf8()
    print("tcms-ai-testgen 全流程演示")
    print(f"python: {py}")
    print(f"LLM 幕: {'开（找到 API key）' if has_key else '关'}"
          f"{'（--llm 但无 key，跳过）' if args.llm and not key else ''}")

    # 幕 1：真实资产（内联精简摘要，避免 demo_assets 全量输出刷屏）
    print(f"\n{'=' * 62}\n▶ 幕1 资产加载：真实 tcms.dbc + 104 场景 + 636 金标\n{'=' * 62}")
    _run("资产解析摘要", [py, "-X", "utf8", "-c",
         "from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets;"
         "b=load_assets(default_upstream_root()/'tcms', default_upstream_root()/'scenarios');"
         "s=b.stats;"
         "print(f'DBC {s.parsed_dbc}/{s.dbc_count} | 场景 {s.parsed_yaml}/{s.yaml_count} | "
         "报文 {b.message_total} | 信号 {b.signal_total} | parse_rate {s.parse_rate:.0%}')"])
    _run("金标检索样例：'车门故障' -> 上游真实用例",
         [py, "-X", "utf8", "-c",
          "from tcms_ai_testgen.rag import GoldenIndex;"
          "from tcms_ai_testgen.asset_loader import default_upstream_root;"
          "i=GoldenIndex.from_tests_dir(default_upstream_root()/'tests');"
          "[print(' ', h.file, '::', h.name, '|', (h.docstring or '')[:44]) for h in i.retrieve('车门故障 DoorControl Fault')]"])

    # 幕 2-4：生成 + 真实执行 + 变异杀毒（mock 源，离线）
    _run(f"幕2-4 全链路：mock 生成 {args.num} 条 → 真实 pytest → 3 变异杀毒",
         [py, str(EX / "demo_full_loop.py"), "--source", "mock_llm",
          "--num", str(args.num), "--mutation"])

    # 幕 2b：生成源对比（规则基线 vs mock——质量可区分的证据）
    _run("幕2b 生成源对比：规则基线 mutant_coverage 1/3 vs mock 3/3",
         [py, str(EX / "run_p3_comparison.py")])

    # 幕 5：反思闭环（需 key）
    if has_key:
        ret = _run("幕5 反思闭环：真 LLM 生成 → 失败 → AI 自愈（self_heal_rate）",
                   [py, str(EX / "run_reflect_demo.py"), "--model", args.llm_model,
                    "--num", "8", "--seed-note", "demo-tour"], timeout=600)
        if ret != 0:
            print("   [提示] LLM 幕失败通常是模型输出格式漂移——重跑一次即可；")
            print("   AI 输出不稳定本身也是实验结论（见 docs/experiments/p4-agent.md）")
    else:
        print("\n" + "=" * 62)
        print("▶ 幕5 反思闭环：跳过（需 DASH_API_KEY）")
        print("   设置 key 后单独跑：python examples/run_reflect_demo.py --num 8")
        print("=" * 62)

    print("\n" + "★" * 62)
    print("演示结束。讲解话术见 docs/interview_guide.md；")
    print("LLM 单幕可分别跑：run_llm_arm / run_reflect_demo / run_judge_compare")
    print("★" * 62)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
