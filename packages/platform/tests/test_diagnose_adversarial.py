"""P1-3 失败样本对抗集门禁：诚实与不发明的第二道机器锁。

数据：domain/data/diagnose_adversarial.yaml（四类：同域易混 / 跨域近义 /
干扰词无码症状 / 空白胡话）。门禁 = 不得从 no_match 变发明、不得错域、
不得输出候选外故障键；expect_no_match 必须有诚实引导语。

纪律：数据里打 [P1-3-fix] 的条目在修复前已实测会误匹配/错域（见 yaml note），
先证会错再纳入 —— 本测试不是橡皮图章。
"""

from __future__ import annotations

from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@NEEDS_UPSTREAM
def _kb():
    """真实资产 + enrich 的 (m, g, retriever)。"""
    from tcms_ai_platform.core import load_asset_model
    from tcms_ai_platform.domain import enrich_graph
    from tcms_ai_platform.knowledge import (
        HybridRetriever,
        VectorStore,
        build_docs_from_asset,
        build_knowledge_graph,
    )

    m = load_asset_model(UPSTREAM)
    g = build_knowledge_graph(m)
    vs = VectorStore()
    vs.add_many(build_docs_from_asset(m))
    enrich_graph(g, vs)
    return m, g, HybridRetriever(vs, g)


@NEEDS_UPSTREAM
def test_diagnose_adversarial_all_pass():
    """对抗集全过：同域易混/跨域近义/干扰词/空白胡话四类 + 全条目不编造。"""
    from tcms_ai_platform.agent.evals import evaluate_adversarial, load_adversarial

    m, g, hr = _kb()
    entries = load_adversarial()
    assert entries, "对抗集为空（yaml 未加载）"
    ev = evaluate_adversarial(m, g, hr, entries)
    fails = [r for r in ev["rows"] if not r["pass"]]
    assert ev["total"] == len(entries)
    assert ev["passed"] == ev["total"], (
        f"对抗集 {ev['passed']}/{ev['total']} 未全过:\n"
        + "\n".join(
            f"  [{r['category']}] {r['q']!r} expect={r['expect']} -> "
            f"key={r['matched_key']!r} reasons={r['reasons']}"
            for r in fails
        )
    )
    # 红线补充：任何一条都不允许编造故障键
    assert all(not r["fabricated"] for r in ev["rows"])


@NEEDS_UPSTREAM
def test_diagnose_adversarial_has_categories_and_fixed_bugs():
    """对抗集结构自检：四类齐备；[P1-3-fix] 回归修复条目存在且期望为 no_match。"""
    from tcms_ai_platform.agent.evals import load_adversarial

    entries = load_adversarial()
    cats = {e["category"] for e in entries}
    assert {"A", "B", "C", "D"} <= cats, f"缺对抗类别: {sorted(cats)}"
    fixed = [e["q"] for e in entries if "P1-3-fix" in e.get("note", "")]
    assert fixed, "对抗集应包含 [P1-3-fix] 回归修复条目（先证会错再修的纪律证据）"
    for q in fixed:
        e = next(x for x in entries if x["q"] == q)
        assert e.get("expect_no_match"), f"修复条目应为 no_match 期望: {q!r}"
