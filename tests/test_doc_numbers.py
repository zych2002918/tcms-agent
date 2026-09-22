"""文档里的数字必须与实物一致——把「漂移即红」从 ADR 条数扩展到资产规模与用例数。

## 为什么需要这个文件

2026-09-21 的一次巡检抓到 12 处文档-实物漂移，**每一处都本可由机器判定**：
全仓用例数写 1615 而实测 1652、engine `safety_case.md` §3 表格逐行相加只有 921
却宣称 960、`CONTRIBUTING.md` 停在 802 用例、platform 的 303 passed 早已是 340、
testgen 覆盖率停在 91.45%……它们全靠人记得改，于是必然忘记。

`test_release_hygiene.py` 已经为「ADR 条数」与「页面清单」立了规矩——
**"能被机器判定的声明，就不要靠人记得改"**。本文件把同一条规矩覆盖到
**资产规模**与**用例数**这两类最容易随功能一起漂移的数字上。

## 设计取舍

- **零依赖**：与 `test_release_hygiene.py` 同款——只读文件、只用 pytest 自身的
  收集结果，不 import 任何成员包。CI 的 lint job 只装了 pytest，本文件必须在那里也能跑。
- **数据缺失时 skip 而非 fail**：上游目录不在（分发场景），或本次只跑了子集
  （`pytest tests/`）时，收集数本来就不代表全仓，如实跳过而不是误报。
- **措辞变了要同步正则**：声明文本由人维护，因此找不到声明时是 fail（而不是 skip）
  ——"文档里不再提这个数字"本身也值得确认一次。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "packages" / "engine"

needs_engine = pytest.mark.skipif(not ENGINE.is_dir(), reason=f"上游 engine 不在: {ENGINE}")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 资产规模：文档声称的条数 == 资产文件里的实际条数
# ---------------------------------------------------------------------------


@needs_engine
def test_documented_asset_scale_matches_assets() -> None:
    """platform README 的资产规模声明必须等于实物。

    这些数字是"项目有多大"的门面，也是最容易在扩资产时忘记同步的地方
    （本轮就抓到场景数与故障键数在多份文档里停留在旧值）。
    """
    doc = _read("packages/platform/README.md")

    faults_text = (ENGINE / "tcms" / "faults.yaml").read_text(encoding="utf-8")
    faults = len(re.findall(r"^\s*-\s*fid:", faults_text, re.M))
    scenarios = len(list((ENGINE / "scenarios").glob("*.yaml")))
    dbc = (ENGINE / "tcms" / "tcms.dbc").read_text(encoding="utf-8", errors="replace")
    messages = len(re.findall(r"^BO_ ", dbc, re.M))
    signals = len(re.findall(r"^\s+SG_ ", dbc, re.M))
    rtm = (ENGINE / "tests" / "rtm.csv").read_text(encoding="utf-8")
    requirements = len(set(re.findall(r"\bSR-\d+\b", rtm)))

    claims = [
        (r"(\d+)\s*条\s*FMEA\s*故障", faults, "FMEA 故障条数"),
        (r"(\d+)\s*个可执行场景", scenarios, "可执行场景数"),
        (r"(\d+)\s*报文", messages, "DBC 报文数"),
        (r"(\d+)\s*信号", signals, "DBC 信号数"),
        (r"(\d+)\s*安全需求", requirements, "安全需求条数"),
    ]
    for pattern, actual, label in claims:
        found = re.findall(pattern, doc)
        assert found, f"文档里找不到「{label}」的声明（正则 {pattern}）—— 改了措辞请同步本测试"
        for claimed in found:
            assert int(claimed) == actual, f"{label}漂移：文档写 {claimed}，实际 {actual}"


# ---------------------------------------------------------------------------
# 2. 用例数：文档声称的数字 == 本次实际收集到的用例数
# ---------------------------------------------------------------------------


def _collected_by_package(items: list[Any]) -> dict[str, int]:
    """按 ``packages/<成员>`` 归组收集到的用例数；根 tests/ 记为 ``root``。"""
    counts: dict[str, int] = {}
    for it in items:
        path = getattr(it, "path", None)
        if path is None:  # pytest < 7 的兜底（本仓要求 >= 8，留一手不影响）
            path = it.fspath
        parts = Path(str(path)).parts
        if "packages" in parts:
            idx = parts.index("packages")
            key = parts[idx + 1] if idx + 1 < len(parts) else "?"
        else:
            key = "root"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _row_for(doc: str, label: str) -> str:
    """取 README 测试表里以给定成员名开头的那一行。"""
    for line in doc.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 2 and cells[1].strip("*") == label:
            return line
    raise AssertionError(f"README 的测试门禁表里找不到「{label}」行")


def _claimed_total(row: str, label: str) -> int:
    """从表格行里解出它声称的用例总数（collected 直接给；passed(+skipped) 求和）。"""
    m = re.search(r"(\d+)\s*collected", row)
    if m:
        return int(m.group(1))
    mp = re.search(r"(\d+)\s*passed", row)
    assert mp, f"「{label}」行里既没有 collected 也没有 passed 声明：{row.strip()}"
    total = int(mp.group(1))
    ms = re.search(r"(\d+)\s*skipped", row)
    if ms:
        total += int(ms.group(1))
    return total


def test_documented_per_package_counts_match_collection(request: pytest.FixtureRequest) -> None:
    """README 表格里每个成员声称的用例数，必须等于该成员实际被收集到的数量。"""
    by_pkg = _collected_by_package(list(request.session.items))
    if "engine" not in by_pkg:
        pytest.skip(f"仅在全仓收集时校验（本次只收集到 {sorted(by_pkg)}）")

    doc = _read("README.md")
    for label in ("engine", "platform", "testgen", "agent"):
        claimed = _claimed_total(_row_for(doc, label), label)
        actual = by_pkg.get(label, 0)
        assert claimed == actual, (
            f"{label} 用例数漂移：README 声称 {claimed}，实际收集 {actual}"
        )


def test_documented_full_suite_count_matches_collection(request: pytest.FixtureRequest) -> None:
    """README 的「全仓 X passed + Y skipped」必须等于本次收集到的用例总数。

    这条守的是**最容易被写错、也最常被引用**的那个数字：本轮它就停在 1615
    （当时真实 1652）——因为"全仓"是所有人引用的口径，却没有任何东西检查它。
    """
    items = list(request.session.items)
    by_pkg = _collected_by_package(items)
    if "engine" not in by_pkg:
        pytest.skip(f"仅在全仓收集时校验（本次只收集到 {sorted(by_pkg)}）")

    row = _row_for(_read("README.md"), "全仓")
    claimed = _claimed_total(row, "全仓")
    actual = len(items)
    assert claimed == actual, (
        f"全仓用例数漂移：README 声称 {claimed}，实际收集 {actual}"
        f"（按成员：{dict(sorted(by_pkg.items()))}）"
    )
