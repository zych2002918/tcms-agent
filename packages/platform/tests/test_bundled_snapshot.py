"""内置资产快照（_assets/）回归测试 —— 防「上游加资产、快照忘同步」漂移。

背景（r3 审计发现）：上游 tcms-can-test 新增 6 场景后，平台内置快照仍是 13 个旧场景，
而 README 声称「22 场景随包分发」——新人只 clone 平台仓库（bundled 模式）会看到与文档
不符的资产数。本测试把「快照 == 上游场景集合」固化为门禁：今后往上游加任何场景，
必须同步进 src/tcms_ai_platform/_assets/scenarios/，否则 CI 红。

快照加载不依赖上游存在（bundled 模式自足）；与上游比对时上游缺失则 skip。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.core.loader import load_from_source
from tcms_ai_platform.core.sources import (
    ASSETS_DIR,
    DBC_REL,
    FAULTS_REL,
    RTM_REL,
    SCENARIOS_REL,
    AssetSource,
)

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


def _bundled_source() -> AssetSource:
    """构造 bundled 模式资产源（显式路径，不走环境解析，测试自足）。"""
    return AssetSource(
        root=None,
        mode="bundled",
        dbc=ASSETS_DIR / "tcms.dbc",
        faults=ASSETS_DIR / "faults.yaml",
        scenarios_dir=ASSETS_DIR / "scenarios",
        rtm=ASSETS_DIR / "tests" / "rtm.csv",
        engine_available=False,
        engine_hint="test-bundled",
    )


def test_bundled_snapshot_files_present():
    """快照关键文件存在（DBC/故障字典/场景目录/RTM）。"""
    assert (ASSETS_DIR / "tcms.dbc").is_file()
    assert (ASSETS_DIR / "faults.yaml").is_file()
    assert (ASSETS_DIR / "scenarios").is_dir()
    assert (ASSETS_DIR / "tests" / "rtm.csv").is_file()


def test_bundled_snapshot_loads_22_scenarios():
    """快照可独立加载：104 场景 + 203 故障 + 22 报文 + 52 需求 + 11 功能（Q2-P-A 口径）。"""
    m = load_from_source(_bundled_source())
    s = m.stats()
    assert s["scenarios"] == 104
    assert s["faults"] == 203
    assert s["messages"] == 22
    assert s["signals"] == 116
    assert s["req_ids"] == 52
    assert s["functions"] == 11
    assert m.load_stats["bad"] == []


def test_bundled_snapshot_contains_r3_new_scenarios():
    """r3 新增 6 场景必须在快照里（新人 clone 单仓库即可见）。"""
    m = load_from_source(_bundled_source())
    expected = {
        "eb_failure_mode_variant.yaml",
        "heartbeat_traction_overspeed_triple.yaml",
        "overspeed_reinject_repeat.yaml",
        "speed_drift_overspeed_escalation.yaml",
        "timetable_multi_node_timing.yaml",
        "turnaround_pantograph_bus_combo.yaml",
    }
    assert expected <= set(m.scenarios), f"快照缺场景: {expected - set(m.scenarios)}"


@NEEDS_UPSTREAM
def test_bundled_snapshot_scenario_set_matches_upstream():
    """快照场景集合 == 上游场景集合（名称级，防漂移的核心断言）。"""
    up_names = {f.name for f in (UPSTREAM / SCENARIOS_REL).glob("*.yaml")}
    bun_names = {f.name for f in (ASSETS_DIR / "scenarios").glob("*.yaml")}
    assert bun_names == up_names, (
        f"快照与上游场景漂移 — 快照缺: {sorted(up_names - bun_names)}; 快照多出: {sorted(bun_names - up_names)}。"
        " 请同步：copy 上游 scenarios/*.yaml → src/tcms_ai_platform/_assets/scenarios/"
    )


@NEEDS_UPSTREAM
def test_bundled_snapshot_faults_identical_to_upstream():
    """快照故障字典与上游字节级一致（防双源漂移）。"""
    up = (UPSTREAM / FAULTS_REL).read_bytes()
    bun = (ASSETS_DIR / "faults.yaml").read_bytes()
    assert up == bun, "快照 faults.yaml 与上游不一致，请同步"


@NEEDS_UPSTREAM
def test_bundled_snapshot_dbc_identical_to_upstream():
    """快照 DBC 与上游字节级一致。"""
    up = (UPSTREAM / DBC_REL).read_bytes()
    bun = (ASSETS_DIR / "tcms.dbc").read_bytes()
    assert up == bun, "快照 tcms.dbc 与上游不一致，请同步"


@NEEDS_UPSTREAM
def test_bundled_snapshot_rtm_identical_to_upstream():
    """快照 RTM 与上游字节级一致。"""
    up = (UPSTREAM / RTM_REL).read_bytes()
    bun = (ASSETS_DIR / "tests" / "rtm.csv").read_bytes()
    assert up == bun, "快照 tests/rtm.csv 与上游不一致，请同步"
