"""文档自证门禁的**执行层与仪器自证**。

`scripts/check_claims.py` 是尺子，`docs/claims.toml` 是被测的声称。本文件做三件事：

1. **执法**：把登记表里的每条声称与实物对齐；authority 级不一致 => 红。
2. **证明尺子没坏**：把一份文档故意改错，门禁**必须**报漂移——否则你会在几个月后
   发现门禁早就静默失效了（这正是 ADR-016「仪器不对」的教训用在门禁自己身上）。
3. **防漏登记**：登记表必须覆盖 README / ARCHITECTURE / 口径卡这三个权威面，
   以及每个在场源文件上的探针都不许返回空值。

零依赖：与 `test_doc_numbers.py` 同款，只 import 标准库 + pytest，
因此能在 CI 的 lint job（只装了 pytest）里跑。
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_claims.py"


def _load_checker():
    """按文件路径装载 scripts/check_claims.py（不依赖 sys.path，不受 import-mode 影响）。"""
    spec = importlib.util.spec_from_file_location("_tcms_check_claims", CHECKER)
    assert spec and spec.loader, f"无法装载 {CHECKER}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_checker()


# ---------------------------------------------------------------------------
# 采集：把本次 pytest 的收集结果喂给门禁（与 test_doc_numbers.py 同一口径）
# ---------------------------------------------------------------------------


def _collected(request: pytest.FixtureRequest) -> dict[str, Any] | None:
    """全仓收集时返回 {total, by_package}；只跑了子集时返回 None（相关声称如实跳过）。

    判据与既有测试一致：engine 不在收集结果里 = 本次不是全仓口径。
    """
    by_package: dict[str, int] = {}
    items = list(request.session.items)
    for it in items:
        path = getattr(it, "path", None) or it.fspath  # type: ignore[attr-defined]
        parts = Path(str(path)).parts
        key = parts[parts.index("packages") + 1] if "packages" in parts else "root"
        by_package[key] = by_package.get(key, 0) + 1
    if "engine" not in by_package:
        return None
    return {"total": len(items), "by_package": by_package}


@pytest.fixture(scope="module")
def registry():
    return cc.load_registry(ROOT / cc.REGISTRY_REL)


# ---------------------------------------------------------------------------
# 1. 执法
# ---------------------------------------------------------------------------


def test_no_documented_number_drifts(request: pytest.FixtureRequest, registry) -> None:
    """登记表里所有声称必须与实物一致（拿不到数据的项如实跳过，不算通过也不算失败）。"""
    claims, _pending = registry
    findings = cc.check(ROOT, claims, collected=_collected(request))
    blocking = [f for f in findings if f.blocking]
    detail = "\n".join(
        f"  {f.claim} @ {f.file}:{f.line}\n    实测 {f.expected} ｜ 文档 {f.actual}" for f in blocking
    )
    assert not blocking, f"文档自证门禁未过（{len(blocking)} 处）:\n{detail}\n\n跑 `uv run python scripts/check_claims.py --report` 看全表"


# ---------------------------------------------------------------------------
# 2. 仪器自证：尺子必须真的量得出东西
# ---------------------------------------------------------------------------


def test_gate_detects_a_deliberately_wrong_number(tmp_path: Path, registry) -> None:
    """把 README 的全仓用例数改错，门禁**必须**报漂移。

    没有这条，门禁可能因为正则失配、路径写错或探针恒等而长期「全绿」——
    一个恒绿的检查比没有检查更危险。
    """
    claims, _pending = registry
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / cc.REGISTRY_REL, tmp_path / cc.REGISTRY_REL)

    real = (ROOT / "README.md").read_text(encoding="utf-8")
    mutated, n = re.subn(r"\*\*\d+ passed \+ \d+ skipped\*\*", "**9999 passed + 4 skipped**", real, count=1)
    assert n == 1, "README 里找不到「**N passed + M skipped**」声明——措辞改了请同步登记表"
    (tmp_path / "README.md").write_text(mutated, encoding="utf-8")

    findings = cc.check(tmp_path, claims, collected={"total": 1722, "by_package": {}})
    drift = [f for f in findings if f.claim == "tests.full" and f.status == "drift"]
    assert drift, "故意写错的全仓用例数没有被门禁抓住——尺子坏了"
    assert "9999" in drift[0].actual


def test_split_claims_catch_a_bad_split_that_sum_would_miss(tmp_path: Path, registry) -> None:
    """合计对得上、拆分写错的文档必须被抓——证明「钉死拆分」那两层不是摆设。

    场景：收集 1731，真实 1727 passed + 4 skipped。文档写成 1726 + 5（合计仍 1731），
    `tests.full`（合计不变量）会放行，而 `tests.full_passed` / `tests.full_skipped` 必须报错。
    """
    claims, _pending = registry
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / cc.REGISTRY_REL, tmp_path / cc.REGISTRY_REL)

    real = (ROOT / "README.md").read_text(encoding="utf-8")
    mutated, n = re.subn(
        r"\*\*\d+ passed \+ \d+ skipped\*\*", "**1726 passed + 5 skipped**", real, count=1
    )
    assert n == 1
    (tmp_path / "README.md").write_text(mutated, encoding="utf-8")

    collected = {"total": 1731, "by_package": {}, "passed": 1727, "skipped": 4}
    findings = cc.check(tmp_path, claims, collected=collected)

    by_claim = {}
    for f in findings:
        if f.file == "README.md":
            by_claim.setdefault(f.claim, []).append(f.status)
    assert "ok" in by_claim.get("tests.full", []), "合计 1726+5 == 1731，这一层本就该放行"
    assert "drift" in by_claim.get("tests.full_passed", []), "写错的 passed 没被抓"
    assert "drift" in by_claim.get("tests.full_skipped", []), "写错的 skipped 没被抓"


def test_pytest_summary_parser_handles_real_output() -> None:
    """`--full-run` 的解析器必须认得真实尾行——包括没有 skipped 的那种。

    这是「--full-run 靠不靠谱」的另一半：另一半（拆分层真能抓错）见上一条测试。
    样例取自本机真实输出，不是编的。
    """
    with_skip = "1722 passed, 4 skipped, 2 warnings in 218.40s (0:03:38)"
    assert cc.parse_pytest_summary(with_skip) == {"passed": 1722, "skipped": 4, "seconds": 218.40}

    no_skip = "170 passed in 13.54s"
    assert cc.parse_pytest_summary(no_skip) == {"passed": 170, "skipped": 0, "seconds": 13.54}

    with_failure = "1 failed, 1727 passed, 4 skipped, 2 warnings in 231.07s (0:03:51)"
    parsed = cc.parse_pytest_summary(with_failure)
    assert parsed["passed"] == 1727 and parsed["skipped"] == 4, "有失败时也要能解出 passed/skipped"

    assert cc.parse_pytest_summary("no tests ran in 0.01s") == {}, "解不出时返回空，不许瞎猜"

    # 子进程输出解码失败时 stdout 会是 None（Windows + GBK 下真踩过）：
    # 此时必须如实返回空表，而不是抛 AttributeError 把整个门禁带崩。
    assert cc.parse_pytest_summary(None) == {}
    assert cc.parse_pytest_summary("") == {}


def test_every_probe_resolves_when_its_source_is_present(registry, request: pytest.FixtureRequest) -> None:
    """源文件在场时，探针不许返回空值——空值会被当成「数据不可得」而静默跳过。

    跑子集（CI 的 lint job 就是 `pytest tests`）时收集不到分包数量，
    此时按登记表里出现过的包名补桩，好让这条测试考的是**探针与参数本身**，
    而不是「本次跑了哪些包」。
    """
    claims, _pending = registry
    collected = _collected(request)
    if collected is None:
        packages = {c.args["package"] for c in claims if c.probe == "collected_package"}
        collected = {"total": 1, "by_package": {name: 1 for name in packages}}
    cache: dict[str, Any] = {}
    # 这两类依赖当前环境本来就拿不到的输入，合法跳过，不算「空探针」：
    #   coverage_percent —— 需要先跑过 --cov 产出 coverage.json；
    #   passed/skipped_total —— 需要 --full-run（收集阶段 skip 数还没决定）。
    exempt = {"coverage_percent", "passed_total", "skipped_total"}
    hollow: list[str] = []
    for claim in claims:
        if claim.probe in exempt:
            continue
        value = cc.measure(ROOT, claim, collected=collected, cache=cache)
        if value is None:
            hollow.append(f"{claim.id}（探针 {claim.probe}）")
    assert not hollow, "以下 claim 的探针返回空值，等于没在守：" + ", ".join(hollow)


def test_registry_covers_the_authority_surfaces(registry) -> None:
    """登记表必须覆盖三个权威面：README / ARCHITECTURE / 口径卡。"""
    claims, _pending = registry
    files = {s.file for c in claims for s in c.sites}
    for required in ("README.md", "docs/ARCHITECTURE.md", "../../resume/_口径卡_项目数字.md"):
        assert required in files, f"登记表不再覆盖 {required}——覆盖面的收缩必须是有意的"


def test_registry_parses_and_ids_are_unique(registry) -> None:
    claims, pending = registry
    ids = [c.id for c in claims] + [p.id for p in pending]
    assert len(ids) == len(set(ids)), "claim / pending 的 id 有重复"
    assert len(claims) >= 10, f"登记表只有 {len(claims)} 条 claim，像是被误删了"
