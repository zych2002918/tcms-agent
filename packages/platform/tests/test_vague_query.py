"""vague-query（离线宽泛问法推理）门禁：域词×故障句式 → 定向推荐真实故障/场景。

红线：域外/无故障句式 → None，绝不硬推荐；推荐项全部来自真实故障字典与场景库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@NEEDS_UPSTREAM
def _model():
    from tcms_ai_platform.core import load_asset_model

    return load_asset_model(UPSTREAM)


@NEEDS_UPSTREAM
def test_vague_aircon_breaks_recommends_hvac_real_faults():
    from tcms_ai_platform.knowledge.vague import analyze_vague

    m = _model()
    r = analyze_vague(m, "空调坏了")
    assert r is not None
    assert r["kind"] == "vague_domain" and r["domain"] == "hvac"
    assert r["domain_zh"] == "空调暖通"
    assert r["faults"], "应推荐该域真实故障"
    assert all(f["key"] in m.faults_by_key for f in r["faults"])
    assert all(str(f["action"]) in ("warning", "derate", "shutdown", "emergency_brake", "none") for f in r["faults"])
    assert r["scenarios"], "应给出复现场景"
    assert all(s["file"] in m.scenarios for s in r["scenarios"])


@NEEDS_UPSTREAM
def test_vague_door_cannot_open_recommends_door():
    from tcms_ai_platform.knowledge.vague import analyze_vague

    m = _model()
    r = analyze_vague(m, "车门打不开了")
    assert r is not None and r["domain"] == "door" and r["faults"]


def test_vague_out_of_domain_returns_none():
    from tcms_ai_platform.knowledge.vague import analyze_vague

    assert analyze_vague(None, "今天天气不错") is None  # 无域词
    assert analyze_vague(None, "空调") is None  # 有域词但无故障句式
    assert analyze_vague(None, "") is None


@NEEDS_UPSTREAM
def test_vague_rank_puts_scenario_backed_faults_first():
    """排序确定性：有复现场景的故障应排在首位（先给"可验证"的）。"""
    from tcms_ai_platform.knowledge.vague import analyze_vague

    m = _model()
    r = analyze_vague(m, "车门坏了")
    assert r is not None and r["domain"] == "door"
    first = r["faults"][0]["key"]
    assert any(first in s.fault_keys for s in m.scenarios.values()), "首位推荐应有复现场景可验证"
