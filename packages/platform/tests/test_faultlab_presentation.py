"""⑤ FaultLab 演示层：每个真实故障都有可展示的域特征演示文案（无静默空档）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_platform.core import load_asset_model
from tcms_ai_platform.faultlab import _action_zh, _domain_alarm

UPSTREAM = Path(__file__).resolve().parents[2] / "tcms-can-test"
NEEDS_UPSTREAM = pytest.mark.skipif(
    not UPSTREAM.is_dir(), reason=f"上游 tcms-can-test 不存在: {UPSTREAM}"
)


@pytest.fixture(scope="module")
def model():
    return load_asset_model(UPSTREAM)


@NEEDS_UPSTREAM
def test_every_fault_has_domain_alarm_text(model):
    """202/202：每个真实故障（含未手工建档的新域故障）都能生成结构化演示告警。"""
    zh = {_action_zh(a) for a in ("none", "warning", "derate", "emergency_brake", "shutdown")}
    missing = []
    for f in model.faults_by_key.values():
        txt = _domain_alarm(f)
        if len(txt) < 12 or f.name not in txt or not any(z in txt for z in zh):
            missing.append((f.key, txt))
    assert not missing, f"缺演示文案的故障: {missing[:5]}"


@NEEDS_UPSTREAM
def test_domain_alarm_text_marks_schematic_honesty(model):
    """演示文案诚实标注：含"域特征示意/巡航基线"，不把示意当真实。"""
    f = model.fault("aux_converter_fault")
    txt = _domain_alarm(f)
    assert "辅助电源域" in txt and ("示意" in txt)


@NEEDS_UPSTREAM
def test_curated_profile_alarm_still_preferred(model):
    """手工建档故障（如 overspeed）仍走 curated alarm，不被域文案覆盖。"""
    from tcms_ai_platform.faultlab import _profiles

    p = _profiles().get("overspeed")
    assert p and "Overspeed" in p["alarm"]
    assert p["zh"] == "超速"


@NEEDS_UPSTREAM
def test_wave_curated_channel_semantics(model):
    """Wave A/B/C 示例档案通道语义保守正确：运行中门开→EB 施加。"""
    from tcms_ai_platform.faultlab import _profiles

    p = _profiles()["door_open_moving"]
    assert p["eb_request"] is True and p["eb_applied"] is True
    assert p["alarm"] == "运行中门开 DoorOpenMoving"
    assert _profiles()["rear_door_fault"]["door_fault_count"] == 1
