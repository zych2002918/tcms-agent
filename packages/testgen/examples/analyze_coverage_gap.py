"""组合增量分析：手写 960 例（上游）vs 生成 DSL 族的语义覆盖对照。

回答面试追问「你的生成器和 960 手写用例比，增量在哪？」——不空谈，
用主题指纹量化：
    * 上游 960 例的函数名主题分布（按 fault/door/speed/heartbeat/... 关键词）；
    * 生成 DSL 族的主题（encode_bound / simulate_inject / fault_scenario × 10 键）；
    * 输出「手写已覆盖 vs 生成可组合」的对照 JSON，标出生成器可做的
      组合维度增量（fault 键 × 处置动作 × 模式/时序的笛卡尔空间）。

口径说明：上游 960 = pytest collect 的 nodeid 数（含参数化展开，实测
`pytest --collect-only` = 960）；函数 def 数为 635。本工具以函数名做主题
指纹，数字仅用于分布对比，不用于绝对计数。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from tcms_ai_testgen.asset_loader import default_upstream_root
from tcms_ai_testgen.oracle import fault_keys, lookup

#: 主题 → 关键词（函数名小写匹配）
TOPICS: dict[str, list[str]] = {
    "fault": ["fault", "fail", "error", "inj"],
    "door": ["door"],
    "speed": ["speed", "velocity"],
    "heartbeat": ["heartbeat", "watchdog", "node_health"],
    "overspeed": ["overspeed"],
    "encode_bound": ["boundary", "max", "reject", "encode", "overflow", "over_max", "below_min"],
    "bus": ["bus", "frame", "crc", "bit_flip", "arbitration"],
    "brake": ["brake", "ebm", "eb_"],
    "energy": ["soc", "battery", "temp", "energy"],
    "recover": ["recover", "clear", "reset", "restore"],
}


def _topic_of(name: str) -> str:
    low = name.lower()
    for topic, kws in TOPICS.items():
        if any(k in low for k in kws):
            return topic
    return "other"


def analyze_upstream(upstream_root: str | Path) -> dict:
    root = Path(upstream_root)
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        raise FileNotFoundError(f"上游 tests 目录不存在: {tests_dir}")
    names: list[str] = []
    for f in sorted(tests_dir.glob("test_*.py")):
        txt = f.read_text(encoding="utf-8")
        names.extend(re.findall(r"^def (test_\w+)", txt, re.M))
    dist = Counter(_topic_of(n) for n in names)
    return {"test_functions": len(names), "topic_distribution": dict(sorted(dist.items()))}


def analyze_generator() -> dict:
    """生成 DSL 族的能力面（10 故障键 × oracle 处置 + 信号注入族）。"""
    keys = fault_keys()
    actions = Counter(lookup(k).action for k in keys)
    return {
        "fault_keys": keys,
        "fault_action_dist": dict(sorted(actions.items())),
        "families": ["encode_bound", "simulate_inject", "fault_scenario"],
        "combinatorial_dimensions": {
            "fault_x_action": {k: lookup(k).action for k in keys},
            "note": "当前覆盖 fault×action 矩阵（auto 模式一列）；fault×mode×时序 "
                    "组合空间未实现（docs/decisions.md D3），是下一步而非已完成的增量",
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="手写 vs 生成的语义覆盖对照")
    p.add_argument("--out", default=None, help="JSON 输出路径")
    args = p.parse_args()

    root = default_upstream_root()
    if not (root / "tests").is_dir():
        print(f"[!] 上游仓库不可用: {root}")
        return 1
    up = analyze_upstream(root)
    gen = analyze_generator()
    report = {
        "upstream_manual": up,
        "generator_dsl": gen,
        "observation": (
            "手写例以 fault/bus/brake/模块行为为主；生成 DSL 提供 fault×action 的"
            "系统化组合（含 mode 敏感处置维度），二者是互补而非替代："
            "手写=金标深度，生成=组合广度。量化集合差需上游 rtm.csv（future work）。"
        ),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n[report] {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
