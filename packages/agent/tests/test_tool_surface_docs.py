"""工具面的数字必须与文档一致——把「功能落地了、docstring 停在规划期」变成红灯。

## 为什么需要

2026-09-21 巡检发现 agent 包里有**三处同类过期描述**：

- `permissions.py`：「M1 只落地 R0。R1/R2/R3 在后续里程碑逐步开放」
- `tools/__init__.py`：「R0 只读 ✅ 7 个 / R1、R2、R3 ⬜ 计划于 M3」
- `graph.build_registry()`：「R0 只读 ✅ 7 个 / R1 沙箱写 ⬜ 计划于 R4」

而四级其实早已全部落地（15 个工具）。这类漂移的危险不在"数字旧了"，而在于
**它恰好出现在最需要被信任的地方**：权限设计是安全关键域 Agent 的核心声明，
读者（以及面试官）会据此**低估工具面的真实权限范围**——把"已经能真执行、
能写记忆"误读成"还只有只读"。

措辞由人维护，但**注册表是权威且可实例化的**：这里断言"注册表实际长什么样
== 文档声称长什么样"，并禁止工具面文档里再出现未落地标记。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from tcms_agent.config import AgentConfig
from tcms_agent.graph import build_registry
from tcms_agent.knowledge import KnowledgeContext

ROOT = Path(__file__).resolve().parents[3]
TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "tcms_agent" / "tools"
AGENT_SRC = Path(__file__).resolve().parents[1] / "src" / "tcms_agent"

#: 与 README 的「四级工具权限（已全部落地，共 15 个工具）」表逐级对齐。
DOCUMENTED_BY_LEVEL = {0: 9, 1: 1, 2: 3, 3: 2}
DOCUMENTED_TOTAL = sum(DOCUMENTED_BY_LEVEL.values())


def _registry(knowledge: KnowledgeContext, cfg: AgentConfig):
    return build_registry(knowledge, cfg, run_id="doc-surface-check")


def test_tool_level_distribution_matches_documented(
    knowledge: KnowledgeContext, cfg: AgentConfig
) -> None:
    """四级权限的工具数必须与 README 声称的一致（总数 + 逐级分布）。

    只断言总数是不够的：把 R0 的一个工具挪到 R2（收紧权限）时总数不变，
    但"哪些工具需要审批"这件事已经变了——那正是最该被文档同步的部分。
    """
    reg = _registry(knowledge, cfg)
    by_level = Counter(int(d["level"]) for d in reg.describe())

    assert len(reg) == DOCUMENTED_TOTAL, (
        f"工具总数漂移：文档声称 {DOCUMENTED_TOTAL}，实测 {len(reg)}"
    )
    assert dict(sorted(by_level.items())) == DOCUMENTED_BY_LEVEL, (
        f"四级分布漂移：文档声称 {DOCUMENTED_BY_LEVEL}，实测 {dict(sorted(by_level.items()))}"
    )


def test_readme_tool_count_claim_matches_registry(
    knowledge: KnowledgeContext, cfg: AgentConfig
) -> None:
    """README 里写死的「共 N 个工具」必须等于注册表实际长度。"""
    reg = _registry(knowledge, cfg)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"共\s*(\d+)\s*个工具", readme)
    assert m, "README 里找不到「共 N 个工具」的声明——改了措辞请同步本测试"
    assert int(m.group(1)) == len(reg), (
        f"README 声称 {m.group(1)} 个工具，注册表实际 {len(reg)} 个"
    )


def test_level_labels_are_reported_for_every_tool(
    knowledge: KnowledgeContext, cfg: AgentConfig
) -> None:
    """`describe()` 是 CLI `tools` 与文档的共同数据源，每一行都必须带级别标签。

    否则 `tcms-agent tools` 的输出会缺列，而"哪一级要审批"正是它存在的理由。
    """
    reg = _registry(knowledge, cfg)
    rows = reg.describe()
    assert rows, "注册表没有工具"
    for row in rows:
        assert row.get("level_label"), f"工具 {row.get('name')} 缺少级别标签"
        assert row["level"] in DOCUMENTED_BY_LEVEL, (
            f"工具 {row.get('name')} 的级别 {row['level']} 不在文档化的四级里"
        )


def test_no_stale_planned_markers_in_tool_surface_docs() -> None:
    """工具面文档里不应再出现「计划于 M3/R4」「⬜」这类未落地标记。

    四级已全部落地；留着这些标记会让读者以为权限体系还没做完——
    这正是本轮修掉的三处漂移的共同形态。新增里程碑若确要写计划，
    请写在 `docs/ARCHITECTURE.md` 的「尚未落地」清单里，而不是工具面注释中。
    """
    files = [
        TOOLS_DIR / "__init__.py",
        TOOLS_DIR / "registry.py",
        AGENT_SRC / "graph.py",
        AGENT_SRC / "permissions.py",
    ]
    stale = re.compile(r"计划于\s*[MR]\d|⬜|待开放|后续里程碑")
    offenders: list[str] = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if stale.search(line):
                offenders.append(f"{f.name}:{i}: {line.strip()[:70]}")
    assert offenders == [], f"工具面文档里残留未落地标记：{offenders}"
