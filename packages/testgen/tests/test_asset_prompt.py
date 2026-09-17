"""asset_prompt 测试：资产事实 → prompt 上下文的构造。"""

from __future__ import annotations

from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets
from tcms_ai_testgen.asset_prompt import build_prompt_context, signal_facts_from_bundle


class TestSignalFacts:
    def test_from_real_bundle(self) -> None:
        root = default_upstream_root()
        if not (root / "tcms" / "tcms.dbc").is_file():
            import pytest

            pytest.skip(f"上游仓库不可用: {root}")
        bundle = load_assets(root / "tcms", root / "scenarios")
        facts = signal_facts_from_bundle(bundle, max_signals=40)
        assert len(facts) > 0
        # 门状态是枚举信号，应带 enum_texts
        door = [f for f in facts if f["signal"] == "Door2State"]
        assert door and door[0]["enum_texts"] == ["Closed", "Open", "Fault", "Unknown"]
        # 数值信号不带枚举
        speed = [f for f in facts if f["signal"] == "SpeedKmh"]
        assert speed and speed[0]["enum_texts"] == []
        assert speed[0]["max"] == 200.0

    def test_enum_priority_and_cap(self) -> None:
        root = default_upstream_root()
        if not (root / "tcms" / "tcms.dbc").is_file():
            import pytest

            pytest.skip(f"上游仓库不可用: {root}")
        bundle = load_assets(root / "tcms", root / "scenarios")
        facts = signal_facts_from_bundle(bundle, max_signals=10)
        assert len(facts) <= 10
        # 有枚举的信号应优先出现
        assert any(f["enum_texts"] for f in facts)


class TestPromptContext:
    def test_build_without_bundle_fallback(self) -> None:
        ctx = build_prompt_context(None)
        assert "DoorControl" in ctx
        assert "Fault" in ctx
        assert "overspeed" in ctx  # oracle 故障键

    def test_build_with_real_bundle(self) -> None:
        root = default_upstream_root()
        if not (root / "tcms" / "tcms.dbc").is_file():
            import pytest

            pytest.skip(f"上游仓库不可用: {root}")
        bundle = load_assets(root / "tcms", root / "scenarios")
        ctx = build_prompt_context(bundle)
        assert "VehicleSpeed" in ctx
        assert "door_fault" in ctx or "overspeed" in ctx
        assert len(ctx) < 5000  # 控制 prompt 长度
