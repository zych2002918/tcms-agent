"""记忆 A/B 实验：把 ADR-016 的负结果变成**可复跑的实验**。

## 为什么需要单独一个仪器

ADR-016 的结论是"记忆在这套任务集上**测不出差异**"，并诚实标注原因是**仪器不对**：

- 原任务集里的目标，LLM 靠工具描述与 DSL 参考就能解决，**不需要历史**；
- 离线规则臂更是结构上无效——它的技能是写死的脚本，有没有记忆都走同一条路
  （实测六项指标完全相同）。

所以"记忆到底有没有用"这个问题，在原有仪器上永远测不出来。本脚本换一个仪器：
**同一批同族任务重复跑 N 轮**。第一轮结束时记忆才有内容，第 2/3 轮才可能受益；
若记忆有用，应当看到**后续轮次的步数/工具调用下降**（或达成率上升）。

## 用法

    set DASH_API_KEY=...                                   # Windows（需真模型）
    uv run python -X utf8 packages/agent/examples/run_memory_ab.py --rounds 3 --limit 4

无 key 时**如实跳过并返回 0**，不假装跑过。

## 诚实边界（沿用 docs/metrics.md 的口径纪律）

- 只跑 `verify` 类同族任务（"验证 X 必须触发 Y 处置"）——只有同族才谈得上"学到做法"；
- n 很小，结论只作**方向性**证据，**不宣称统计显著性**；
- 三种结果都如实报告：**下降**=记忆有用；**持平**=连这个仪器也测不出；
  **上升**=记忆注入反而拖累。三者都比"仪器不对"更有信息量。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: 只在同族任务上才谈得上"从历史学到做法"，因此默认限 verify 类。
FAMILY_KIND = "verify"


def _fmt(metrics: dict) -> str:
    keys = ("match_rate", "avg_steps", "avg_tool_calls", "tool_failure_rate", "hallucination_rate")
    parts = []
    for k in keys:
        v = metrics.get(k)
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        elif v is not None:
            parts.append(f"{k}={v}")
    return "  ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆 A/B：同族任务多轮，看后续轮次是否受益")
    ap.add_argument("--rounds", type=int, default=3, help="重复轮数（>1 才可能测出记忆）")
    ap.add_argument("--limit", type=int, default=4, help="同族任务条数上限")
    ap.add_argument("--out", default="", help="报告 JSON 落盘路径（可选）")
    ap.add_argument("--workdir", default="", help="实验目录（默认临时目录，便于复跑对照）")
    args = ap.parse_args()

    from tcms_agent.config import AgentConfig
    from tcms_agent.eval.harness import Arm, run_arm
    from tcms_agent.eval.tasks import load_tasks
    from tcms_agent.knowledge import build_knowledge
    from tcms_agent.models import llm_available

    if not llm_available():
        print("未检测到可用的 LLM key（DASH_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / "
              "LLM_API_KEY，或本机设置 / DSH 凭据文件）。")
        print("本实验**必须**用真模型：记忆的收益在离线规则臂上结构上不可测（ADR-016）。")
        print("→ 如实跳过（这不是失败）。")
        return 0

    tasks = [t for t in load_tasks() if t.kind == FAMILY_KIND][: args.limit]
    if not tasks:
        print(f"任务集里没有 {FAMILY_KIND} 类任务，无法做同族对照。")
        return 0

    import tempfile

    root = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="tcms-mem-ab-"))
    root.mkdir(parents=True, exist_ok=True)
    knowledge = build_knowledge()

    print(f"任务：{len(tasks)} 条（kind={FAMILY_KIND}）· 轮数：{args.rounds} · 目录：{root}")
    for t in tasks:
        print(f"  - {t.id}: {t.goal}")

    arms = (
        Arm("llm", {"offline": False}, requires_llm=True),
        Arm("llm-no-memory", {"offline": False, "memory_enabled": False}, requires_llm=True),
    )

    report: dict = {"tasks": [t.id for t in tasks], "rounds": args.rounds, "arms": {}}
    for arm in arms:
        print(f"\n=== 臂 {arm.name} ===")
        per_round: list[dict] = []
        for rnd in range(1, args.rounds + 1):
            # 逐轮调用、**共享同一 workdir**：记忆因此跨轮累积（这正是要观测的变量）。
            # 不用 run_arm(rounds=N) 是因为它只回最后两轮的达标率，拿不到逐轮步数。
            rep = run_arm(arm, tasks, knowledge=knowledge, workdir=root / arm.name, rounds=1)
            m = dict(rep.metrics)
            per_round.append(m)
            print(f"  round {rnd}: {_fmt(m)}")
        report["arms"][arm.name] = per_round

    # 方向性判断（不宣称显著性）
    print("\n=== 方向性读数 ===")
    for name, rows in report["arms"].items():
        if len(rows) < 2:
            continue
        first, last = rows[0], rows[-1]
        ds = (last.get("avg_steps") or 0) - (first.get("avg_steps") or 0)
        dc = (last.get("avg_tool_calls") or 0) - (first.get("avg_tool_calls") or 0)
        dm = (last.get("match_rate") or 0) - (first.get("match_rate") or 0)
        print(f"  {name}: Δmatch={dm:+.2f}  Δsteps={ds:+.2f}  Δtools={dc:+.2f}（末轮 vs 首轮）")

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已落盘：{args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
