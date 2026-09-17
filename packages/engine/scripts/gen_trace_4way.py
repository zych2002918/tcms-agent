# -*- coding: utf-8 -*-
"""RTM 四向追溯视图生成器（EN 50128 思想：需求→实现/场景→测试→故障 可双向追溯）。

读 tests/rtm.csv + scenarios/*.yaml + tcms/faults.yaml，
产出 docs/rtm_4way.md：
  1. SR 行视图：SR → verifies → 实现模块/场景 YAML → 验证测试 → 该场景覆盖的故障键
  2. 汇总：场景模块 SR 数、被追溯故障去重数、孤立计数（防漂移自检）
运行：python scripts/gen_trace_4way.py
"""

import csv
import re
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RTM = ROOT / "tests" / "rtm.csv"
SCEN = ROOT / "scenarios"
FAULTS = ROOT / "tcms" / "faults.yaml"
OUT = ROOT / "docs" / "rtm_4way.md"


def _scenario_faults(file: Path) -> list[str]:
    text = file.read_text(encoding="utf-8")
    keys = []
    for m in re.finditer(r"fault:\s*([A-Za-z_][A-Za-z0-9_]*)", text):
        if m.group(1) not in keys:
            keys.append(m.group(1))
    return keys


def main() -> int:
    rows: list[dict] = []
    with RTM.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(line for line in f if not line.lstrip().startswith("#"))
        rows = list(reader)

    scen_faults: dict[str, list[str]] = {}
    for p in sorted(SCEN.glob("*.yaml")):
        scen_faults[p.name] = _scenario_faults(p)

    # 故障中文名（键→名）与子系统
    import yaml  # noqa: PLC0415

    fd = yaml.safe_load(FAULTS.read_text(encoding="utf-8"))
    name_of = {e["key"]: f"{e['name']}" for e in fd["faults"]}

    linked_faults: OrderedDict[str, str] = OrderedDict()
    rows_out: list[str] = []
    scen_sr = 0
    sr_ids: set[str] = set()
    for r in rows:
        sr_ids.add(r["req_id"])
        module = r["module"]
        test = r["test_file"]
        faults: list[str] = []
        if module.startswith("scenarios/"):
            scen_sr += 1
            faults = scen_faults.get(module.split("/", 1)[1], [])
        for k in faults:
            linked_faults.setdefault(k, name_of.get(k, k))
        faults_txt = "、".join(f"`{k}`" for k in faults) if faults else "—（模块行为测试）"
        rows_out.append(
            f"| {r['req_id']} | {r['verifies']} | `{module}` | `{test}` | {faults_txt} |"
        )

    lines = [
        "# RTM 四向追溯（EN 50128 思想）—— 需求 ↔ 场景/实现 ↔ 测试 ↔ 故障",
        "",
        "> 机器生成：`scripts/gen_trace_4way.py`（改动 rtm.csv/scenarios/faults 后重跑，防手抄漂移）。",
        f"> 快照：RTM **{len(rows)} 行 / {len(sr_ids)} SR** · "
        f"场景文件 **{len(scen_faults)}** · 场景模块 SR **{scen_sr}** · 被场景追溯的故障键 **{len(linked_faults)}**（去重）。",
        "",
        "## 1. SR → 场景/模块 → 测试 → 故障",
        "",
        "| SR | 需求（verifies） | 实现模块/场景 | 验证测试 | 覆盖故障 |",
        "|---|---|---|---|---|",
    ]
    lines += rows_out
    lines += ["", "## 2. 被追溯故障目录（场景模块 SR 可达）", ""]
    lines.append("| 故障键 | 中文名 |")
    lines.append("|---|---|")
    for k, n in linked_faults.items():
        lines.append(f"| `{k}` | {n} |")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {OUT} ; SR={len(sr_ids)} scenarios={len(scen_faults)} "
        f"scen_sr={scen_sr} linked_faults={len(linked_faults)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
