#!/usr/bin/env python
"""文档自证门禁：把「文档里写的数字与状态」和「实物」逐条对齐。

## 为什么存在

`tests/test_doc_numbers.py` 守住了「资产六数 + 分包用例数」。但 2026-10-07 的一次
外机审计仍然抓到一批**没人守**的漂移，每一处都本可由机器判定：

- 口径卡（自称「唯一权威」）停在 `1652 passed`，而实测 1722；
- `ARCHITECTURE.md` 的「尚未落地」表把**已经实现**的上下文预算标成 ⬜；
- `packages/agent/README.md` 教人用**根本不存在的** `--offline`；
- 检索 golden 已在 09-21 由 14 条扩到 28 条，同文件另一处仍在讲 `12/14 → 14/14`。

它们的共同点：**只靠人记得改，于是必然忘记**。本文件把那条规矩从「数字」
扩到「状态声称」与「接口面」，并给出人用的两种模式：

```
--report   打印漂移表（不改任何文件）——发版前 / 审计时先看这个
--check    有 authority 级不一致即非零退出（CI 与 pytest 用这个）
--fix      把登记位置上的数字就地改成实测值（人只审 diff）
--full-run 真跑一遍全仓，连 passed/skipped 的拆分也钉死
--strict   发版前用：本机本该真测却拿不到数据的项，从「跳过」升级为阻断
           （跳过 ≠ 通过：CI 里跳过是合理的，发版前不是）
```

## 设计取舍

- **零第三方依赖**：只用标准库（含 `tomllib`，本仓 `.python-version` = 3.11）。
  原因是它必须能跑在 CI 的 lint job 里——那里只装了 pytest，不装任何成员包。
- **拿不到数据就 skip，不猜**：上游目录缺失、只跑了子集、`coverage.json` 不存在时
  如实跳过；**「找不到声明」才是 fail**——文档不再提这个数字本身也值得确认一次。
- **登记表是唯一事实源**：`docs/claims.toml`。新增一个对外数字 = 新增一条 claim，
  否则门禁不认识它（`tests/test_doc_claims.py` 里有覆盖率的元测试把这条钉住）。
- **不放 LLM 进环路**：让模型「审文档」不可复现，而且它自己就需要一台仪器（ADR-016）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_DEFAULT = Path(__file__).resolve().parent.parent
REGISTRY_REL = "docs/claims.toml"

AUTHORITY = "authority"
HISTORY = "history"

# 这些探针需要「真环境」才有数：CI 的 lint job 只装 pytest（没成员包），也不跑 --cov。
# 因此默认模式下它们如实**跳过**；但发版前用 `--strict`，跳过会升级成阻断——
# 「本机本该真测却拿不到数据」和「这条不需要测」是两回事，不能混着显示成一片安静。
REQUIRES_SOURCE_PROBES = {"coverage_percent", "passed_total", "skipped_total", "kb_state"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Site:
    """一条声称出现的位置：文件 + 抓取正则（命名组 `value`）。"""

    file: str
    pattern: str | None = None
    forbid: bool = False
    tier: str = AUTHORITY


@dataclass
class Claim:
    id: str
    label: str
    kind: str  # number | forbid | tokens
    probe: str
    args: dict[str, Any] = field(default_factory=dict)
    sites: list[Site] = field(default_factory=list)
    strength: str = "fail"  # fail | warn
    note: str = ""


@dataclass
class Pending:
    """需要人拍板、机器判不了的项——如实列出来，不假装它被守住了。"""

    id: str
    label: str
    why: str


@dataclass
class Finding:
    claim: str
    label: str
    file: str
    line: int
    status: str  # ok | drift | forbidden | missing | skip
    expected: str
    actual: str
    tier: str = AUTHORITY
    strength: str = "fail"

    @property
    def blocking(self) -> bool:
        return self.status in {"drift", "forbidden", "missing"} and (
            self.tier == AUTHORITY and self.strength == "fail"
        )


# ---------------------------------------------------------------------------
# 探针：只读文件 / 只做纯计算，不 import 任何成员包
# ---------------------------------------------------------------------------


def _read(root: Path, rel: str) -> str | None:
    p = (root / rel).resolve()
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def probe_asset_counts(root: Path) -> dict[str, int]:
    """engine 资产实物计数（与 test_doc_numbers.py 同一口径）。

    上游不在（分发场景 / 自证测试指向的临时副本）时返回空表——调用方按「数据不可得」跳过，
    而不是把「文件没有」当成「数字是 0」。
    """
    engine = root / "packages" / "engine"
    if not (engine / "tcms" / "faults.yaml").is_file():
        return {}
    faults_text = (engine / "tcms" / "faults.yaml").read_text(encoding="utf-8")
    dbc = (engine / "tcms" / "tcms.dbc").read_text(encoding="utf-8", errors="replace")
    rtm = (engine / "tests" / "rtm.csv").read_text(encoding="utf-8")
    return {
        "faults": len(re.findall(r"^\s*-\s*fid:", faults_text, re.M)),
        "scenarios": len(list((engine / "scenarios").glob("*.yaml"))),
        "messages": len(re.findall(r"^BO_ ", dbc, re.M)),
        "signals": len(re.findall(r"^\s+SG_ ", dbc, re.M)),
        "requirements": len(set(re.findall(r"\bSR-\d+\b", rtm))),
    }


def probe_file_regex_count(root: Path, file: str, pattern: str) -> str | None:
    text = _read(root, file)
    if text is None:
        return None
    return str(len(re.findall(pattern, text, re.M)))


def probe_status_symbol(root: Path, marker_file: str, marker_pattern: str) -> str | None:
    """实现标记存在 → 该行必须是 ✅；不存在 → 必须是 ⬜。"""
    text = _read(root, marker_file)
    if text is None:
        return None
    return "✅" if re.search(marker_pattern, text) else "⬜"


_KB_CACHE: dict[str, dict[str, dict[str, int]]] = {}


def _kb_snapshot(g: Any, store: Any) -> dict[str, int]:
    gs = g.stats()
    return {"nodes": gs["nodes"], "edges": gs["edges"], "docs": store.stats()["docs"]}


def _kb_build(root: Path) -> dict[str, dict[str, int]] | None:
    """按 `server/app.py:455-465` 的同一顺序建两态：基础（仅资产）/ 服务态（注入领域知识+症状因果）。

    需要 platform 包在场；CI 的 lint job 只装 pytest → ImportError → 如实跳过。
    """
    key = str(root)
    if key in _KB_CACHE:
        return _KB_CACHE[key]
    upstream = root / "packages" / "engine"
    if not upstream.is_dir():
        return None
    try:
        from tcms_ai_platform.core import load_asset_model
        from tcms_ai_platform.domain import enrich_graph
        from tcms_ai_platform.knowledge import (
            VectorStore,
            build_docs_from_asset,
            build_knowledge_graph,
        )
    except ImportError:
        return None
    model = load_asset_model(upstream)
    graph = build_knowledge_graph(model)
    store = VectorStore()
    store.add_many(build_docs_from_asset(model))
    states = {"base": _kb_snapshot(graph, store)}
    enrich_graph(graph, store)
    states["service"] = _kb_snapshot(graph, store)
    _KB_CACHE[key] = states
    return states


def probe_kb_state(root: Path, state: str, key: str) -> str | None:
    """知识底座的节点/边/向量数：state ∈ base|service，key ∈ nodes|edges|docs。"""
    states = _kb_build(root)
    if states is None:
        return None
    return str(states[state][key])


def probe_glob_count(root: Path, dir_: str, pattern: str) -> str:
    """某个目录下匹配的文件数（用于「环境残留物」这类观察项）。"""
    target = root / dir_
    if not target.is_dir():
        return "0"
    return str(len(list(target.glob(pattern))))


def probe_harness_arms(root: Path, file: str, pattern: str) -> list[str] | None:
    """从 harness 源码里抓出臂名清单（评测臂有几只，由代码说了算）。"""
    text = _read(root, file)
    if text is None:
        return None
    block = re.search(pattern, text, re.S)
    if not block:
        return None
    return re.findall(r'Arm\(\s*"([^"]+)"', block.group(0))


def probe_coverage_percent(root: Path, file: str) -> str | None:
    """coverage.json 里的总覆盖率（CI 产出；本地没跑 --cov 时如实跳过）。"""
    text = _read(root, file)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    totals = data.get("totals") or {}
    value = totals.get("percent_covered")
    return None if value is None else f"{value:.2f}"


PROBES = {
    "asset_counts": probe_asset_counts,
    "file_regex_count": probe_file_regex_count,
    "status_symbol": probe_status_symbol,
    "harness_arms": probe_harness_arms,
    "coverage_percent": probe_coverage_percent,
    "glob_count": probe_glob_count,
    "kb_state": probe_kb_state,
}


def measure(
    root: Path,
    claim: Claim,
    *,
    collected: dict[str, Any] | None = None,
    cache: dict[str, Any] | None = None,
) -> str | list[str] | None:
    """求一条 claim 的实测值；拿不到数据返回 None（调用方 skip）。"""
    cache = cache if cache is not None else {}

    if claim.probe == "none":
        # forbid_text 这类纯文本禁令不需要任何实测数据
        return "0"

    if claim.probe == "asset_counts":
        key = "asset_counts"
        if key not in cache:
            cache[key] = probe_asset_counts(root)
        table = cache[key]
        name = claim.args["key"]
        return str(table[name]) if name in table else None

    if claim.probe == "collected_total":
        if collected is None:
            return None
        return str(collected["total"])

    if claim.probe in {"passed_total", "skipped_total"}:
        # 只在 `--full-run` 真跑一遍后才有值：收集阶段拿不到 skip 数（skip 是运行期决定的）。
        if collected is None or collected.get("passed") is None:
            return None
        key = "passed" if claim.probe == "passed_total" else "skipped"
        return str(collected.get(key, 0))

    if claim.probe == "collected_package":
        if collected is None:
            return None
        value = collected["by_package"].get(claim.args["package"])
        return None if value is None else str(value)

    fn = PROBES.get(claim.probe)
    if fn is None:
        raise KeyError(f"未知探针 {claim.probe!r}（claim {claim.id}）")
    if claim.probe in {"file_regex_count", "coverage_percent"}:
        return fn(root, claim.args["file"], claim.args["pattern"]) if "pattern" in claim.args else fn(root, claim.args["file"])  # type: ignore[operator]
    if claim.probe == "status_symbol":
        return fn(root, claim.args["marker_file"], claim.args["marker_pattern"])  # type: ignore[operator]
    if claim.probe == "harness_arms":
        return fn(root, claim.args["file"], claim.args["pattern"])  # type: ignore[operator]
    if claim.probe == "glob_count":
        return fn(root, claim.args["dir"], claim.args["pattern"])  # type: ignore[operator]
    if claim.probe == "kb_state":
        return fn(root, claim.args["state"], claim.args["key"])  # type: ignore[operator]
    raise KeyError(f"探针 {claim.probe!r} 调用方式未定义")


# ---------------------------------------------------------------------------
# 登记表装载
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> tuple[list[Claim], list[Pending]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    claims: list[Claim] = []
    for raw in data.get("claim", []):
        sites = [
            Site(
                file=s["file"],
                pattern=s.get("pattern"),
                forbid=bool(s.get("forbid", False)),
                tier=s.get("tier", AUTHORITY),
            )
            for s in raw.get("sites", [])
        ]
        claims.append(
            Claim(
                id=raw["id"],
                label=raw["label"],
                kind=raw["kind"],
                probe=raw["probe"],
                args=raw.get("args", {}),
                sites=sites,
                strength=raw.get("strength", "fail"),
                note=raw.get("note", ""),
            )
        )
    pending = [
        Pending(id=p["id"], label=p["label"], why=p["why"]) for p in data.get("pending", [])
    ]
    return claims, pending


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def check(
    root: Path,
    claims: list[Claim],
    *,
    collected: dict[str, Any] | None = None,
    strict: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    cache: dict[str, Any] = {}
    for claim in claims:
        if claim.kind == "forbid_text":
            # 纯文本禁令：不依赖任何探针，永远生效（用于「历史口径必须带标注」这类规矩）
            expected: str | list[str] | None = "0"
        else:
            expected = measure(root, claim, collected=collected, cache=cache)
        if claim.kind == "watch":
            # 观察项：没有「文档位置」，只报告状态。永不阻断——
            # 它盯的是「环境残留物会让对外数字只在本机成立」这类事。
            hot = expected is not None and str(expected).isdigit() and int(str(expected)) > 0
            findings.append(
                Finding(
                    claim.id, claim.label, claim.args.get("file") or claim.args.get("dir") or "-", 0,
                    "watch" if hot else "ok", "0", str(expected), AUTHORITY, "warn",
                )
            )
            continue
        for site in claim.sites:
            # 先判「数据可不可得」：严格模式下这本身就是阻断项（不该怪站点文件）
            if expected is None:
                strict_miss = strict and claim.probe in REQUIRES_SOURCE_PROBES
                findings.append(
                    Finding(
                        claim.id, claim.label, site.file, 0,
                        "missing" if strict_miss else "skip",
                        "(严格模式要求本机实测)" if strict_miss else "(数据不可得)",
                        "-", site.tier, claim.strength,
                    )
                )
                continue
            text = _read(root, site.file)
            if text is None:
                findings.append(
                    Finding(claim.id, claim.label, site.file, 0, "skip", str(expected), "文件不存在", site.tier, claim.strength)
                )
                continue

            if claim.kind == "tokens":
                missing = [t for t in expected if t not in text]  # type: ignore[union-attr]
                findings.append(
                    Finding(
                        claim.id, claim.label, site.file, 0,
                        "missing" if missing else "ok",
                        "/".join(expected),  # type: ignore[arg-type]
                        ("缺 " + ", ".join(missing)) if missing else "全在",
                        site.tier, claim.strength,
                    )
                )
                continue

            if claim.kind == "sum":
                # 「N passed + M skipped」这类声明：收集阶段拿不到 skip 数，
                # 因此只校验**合计**（passed + skipped == 收集数）——与既有
                # tests/test_doc_numbers.py 同一不变量。要钉死拆分，用 `--full-run`。
                assert site.pattern is not None, f"{claim.id} 的 site 缺 pattern"
                sums = list(re.finditer(site.pattern, text, re.M))
                if not sums:
                    findings.append(
                        Finding(claim.id, claim.label, site.file, 0, "missing", f"合计 {expected}", "声明未找到", site.tier, claim.strength)
                    )
                    continue
                for m in sums:
                    gd = m.groupdict()
                    passed = (gd.get("value") or "").strip()
                    skipped = (gd.get("skipped") or "0").strip()
                    total_ok = passed.isdigit() and skipped.isdigit() and int(passed) + int(skipped) == int(str(expected))
                    findings.append(
                        Finding(
                            claim.id, claim.label, site.file, _line_of(text, m.start()),
                            "ok" if total_ok else "drift",
                            f"passed+skipped == {expected}", f"{passed} + {skipped}",
                            site.tier, claim.strength,
                        )
                    )
                continue

            assert site.pattern is not None, f"{claim.id} 的 site 缺 pattern"
            matches = list(re.finditer(site.pattern, text, re.M))

            if site.forbid:
                # 「禁现」只在探针确认「代码里确实没有」时才生效：
                # 若哪天真的加上了这个参数（探针 != "0"），门禁改为跳过，
                # 由人去删掉这条 claim —— 而不是继续误伤文档。
                if str(expected) != "0":
                    findings.append(
                        Finding(claim.id, claim.label, site.file, 0, "skip", str(expected), "代码里已存在，claim 需人工复核", site.tier, claim.strength)
                    )
                    continue
                first = matches[0] if matches else None
                findings.append(
                    Finding(
                        claim.id, claim.label, site.file,
                        _line_of(text, first.start()) if first else 0,
                        "forbidden" if first else "ok",
                        f"不得出现 /{site.pattern}/", (first.group(0)[:60] if first else "未出现"),
                        site.tier, claim.strength,
                    )
                )
                continue

            if not matches:
                findings.append(
                    Finding(claim.id, claim.label, site.file, 0, "missing", str(expected), "声明未找到", site.tier, claim.strength)
                )
                continue

            for m in matches:
                got = (m.groupdict().get("value") or "").strip()
                ok = got == str(expected)
                findings.append(
                    Finding(
                        claim.id, claim.label, site.file, _line_of(text, m.start()),
                        "ok" if ok else "drift", str(expected), got, site.tier, claim.strength,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def render(findings: list[Finding], pending: list[Pending]) -> str:
    bad = [f for f in findings if f.status != "ok"]
    lines = [
        "文档自证门禁 —— 声称 vs 实物",
        "=" * 78,
    ]
    if not bad:
        lines.append(f"全部一致（{len(findings)} 处声称，0 处漂移）")
    for f in bad:
        tag = {"drift": "漂移", "forbidden": "违禁", "missing": "缺声明", "skip": "跳过", "watch": "观察"}[f.status]
        star = "!!" if f.blocking else " ~"
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"{star} [{tag}] {f.claim} {f.label}")
        lines.append(f"      位置 {loc}")
        lines.append(f"      实测 {f.expected} ｜ 文档 {f.actual}")
    ok_count = sum(1 for f in findings if f.status == "ok")
    skipped = sum(1 for f in findings if f.status == "skip")
    watched = sum(1 for f in findings if f.status == "watch")
    blocking = [f for f in findings if f.blocking]
    lines += [
        "-" * 78,
        f"一致 {ok_count} ｜ 漂移类 {len(bad) - skipped - watched} ｜ 跳过 {skipped}"
        f" ｜ 观察 {watched} ｜ 阻断 {len(blocking)}",
    ]
    if pending:
        lines += ["", "待人工拍板（机器判不了，如实列出）:"]
        lines += [f"  · {p.id} {p.label}：{p.why}" for p in pending]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# --fix：把登记位置上的数字就地改成实测值
# ---------------------------------------------------------------------------


def fix(root: Path, claims: list[Claim], *, collected: dict[str, Any] | None = None) -> list[str]:
    changed: list[str] = []
    cache: dict[str, Any] = {}
    for claim in claims:
        if claim.kind != "number" or claim.probe == "status_symbol":
            # 状态声称不是「换个符号」那么简单：✅ 与同行正文必须自洽，
            # 机器只负责报冲突，改写交给人（见 docs/claims.toml 的 note）。
            continue
        expected = measure(root, claim, collected=collected, cache=cache)
        if expected is None:
            continue
        for site in claim.sites:
            if site.forbid or site.pattern is None:
                continue
            path = (root / site.file).resolve()
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            original = text
            spans = [m.span("value") for m in re.finditer(site.pattern, text, re.M)]
            if not spans:
                continue
            for start, end in reversed(spans):
                if text[start:end] != expected:
                    text = text[:start] + expected + text[end:]
                    changed.append(f"{site.file}: {claim.id} -> {expected}")
            if text != original:
                # 只在真的改了才回写：无变更也写一遍会让 git/编辑器看到无谓的 mtime 抖动
                path.write_text(text, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------------
# 采集（CLI 用；pytest 里改由 session.items 注入）
# ---------------------------------------------------------------------------


def parse_pytest_summary(text: str | None) -> dict[str, Any]:
    """从 `pytest -q` 的尾行解出 passed / skipped / 耗时。

    单独成函数是为了能被单测直接喂样例（真跑一遍全仓要几分钟，
    而这条解析路径恰恰是「--full-run 到底靠不靠谱」的关键一半）。
    喂进来 None（子进程输出解码失败等）时返回空表，由调用方如实降级。
    """
    if not text:
        return {}
    tail = "\n".join(text.strip().splitlines()[-3:])
    mp = re.search(r"(\d+) passed", tail)
    if not mp:
        return {}
    ms = re.search(r"(\d+) skipped", tail)
    md = re.search(r"in ([\d.]+)s", tail)
    return {
        "passed": int(mp.group(1)),
        "skipped": int(ms.group(1)) if ms else 0,
        "seconds": float(md.group(1)) if md else None,
    }


def collect_via_pytest(root: Path, *, full: bool = False) -> dict[str, Any] | None:
    """采集用例数。

    - 默认只跑 `--collect-only`（快，能给总数与分包数）；**拿不到 skip 数**，
      因此「N passed + M skipped」这类声称只校验合计。
    - `full=True` 额外真跑一遍全仓（数分钟），从尾行解出 passed / skipped / 耗时，
      于是拆分也能被钉死——发版前跑一次这个。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    ids = [ln.strip() for ln in (proc.stdout or "").splitlines() if "::" in ln]
    if not ids:
        return None
    by_package: dict[str, int] = {}
    for node in ids:
        parts = Path(node.split("::", 1)[0]).parts
        key = parts[parts.index("packages") + 1] if "packages" in parts else "root"
        by_package[key] = by_package.get(key, 0) + 1

    result: dict[str, Any] = {"total": len(ids), "by_package": by_package}
    if full:
        try:
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=root, capture_output=True, text=True,
                # Windows 上 text=True 默认按 locale(GBK) 解码，而 pytest 输出里有中文 →
                # 解码异常会让 stdout 直接变成 None（本机真踩过）。显式钉 UTF-8。
                encoding="utf-8", errors="replace", timeout=3600,
            )
        except (OSError, subprocess.SubprocessError):
            return result
        parsed = parse_pytest_summary(run.stdout)
        if parsed:
            result.update(parsed)
            result["exit_code"] = run.returncode
    return result


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，输出里的 ✅/⬜ 会直接抛 UnicodeEncodeError。
    # 自己把 stdout 切到 UTF-8，免得调用方必须记得加 `-X utf8`。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="文档自证门禁：声称 vs 实物")
    ap.add_argument("--root", default=str(ROOT_DEFAULT), help="仓库根（自证测试会指向临时副本）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="只打印漂移表，不做退出码判定")
    mode.add_argument("--check", action="store_true", help="有 authority 级漂移即非零退出")
    mode.add_argument("--fix", action="store_true", help="把登记位置上的数字就地改成实测值")
    ap.add_argument("--no-collect", action="store_true", help="不跑 pytest 采集（用例数相关 claim 跳过）")
    ap.add_argument("--full-run", action="store_true", help="真跑一遍全仓，钉死 passed/skipped 拆分（数分钟）")
    ap.add_argument("--strict", action="store_true", help="发版前用：本机本该真测却拿不到数据的项，从「跳过」升级为阻断")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    claims, pending = load_registry(root / REGISTRY_REL)

    collected = None if args.no_collect else collect_via_pytest(root, full=args.full_run)
    if collected and collected.get("passed") is not None:
        print(
            f"实测：collected {collected['total']} ｜ passed {collected['passed']} ｜ "
            f"skipped {collected['skipped']} ｜ {collected.get('seconds')}s"
        )

    if args.fix:
        for line in fix(root, claims, collected=collected):
            print("已改写 " + line)

    findings = check(root, claims, collected=collected, strict=args.strict)
    if args.strict:
        print("（严格模式：需要本机实测的探针若拿不到数据，一律按阻断计）")
    print(render(findings, pending))

    blocking = [f for f in findings if f.blocking]
    if args.check:
        return 1 if blocking else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
