"""prompt v2 测试：execution DSL 契约 + 资产事实注入。"""

from __future__ import annotations

from tcms_ai_testgen.prompt import (
    build_asset_context,
    build_system_prompt,
    build_user_prompt,
)


class TestSystemPromptContract:
    def test_execution_field_required(self) -> None:
        p = build_system_prompt()
        assert "execution" in p
        assert "必须" in p  # 明确要求
        assert "expect_encode_error" in p
        assert "expect_signal" in p

    def test_whitelist_ops_documented(self) -> None:
        p = build_system_prompt()
        for op in ("set_door_state", "stop_message", "send_alarm", "expect_action",
                   "expect_encode_ok", "expect_no_frames"):
            assert op in p

    def test_raw_decoded_direction_rule(self) -> None:
        p = build_system_prompt()
        assert "raw" in p
        assert "Fault" in p  # 枚举文本示例
        # 明确禁止数字 0-3 作枚举断言
        assert "禁止用数字" in p or "文本" in p

    def test_fewshot_examples_present(self) -> None:
        p = build_system_prompt()
        assert "test_speed_over_max_rejected" in p
        assert "test_door_fault_detected" in p
        assert "test_overspeed_derate" in p

    def test_asset_context_optional(self) -> None:
        ctx = build_asset_context(
            [
                {"message": "DoorControl", "signal": "Door2State", "enum_texts": ["Closed", "Open", "Fault", "Unknown"], "min": 0, "max": 3, "unit": ""},
                {"message": "VehicleSpeed", "signal": "SpeedKmh", "enum_texts": [], "min": 0, "max": 200, "unit": "km/h"},
            ],
            ["overspeed", "door_fault"],
        )
        assert "Door2State" in ctx
        assert "Fault" in ctx
        assert "km/h" in ctx
        assert "overspeed" in ctx
        p = build_system_prompt(ctx)
        assert "已知资产事实" in p


class TestUserPrompt:
    def test_mentions_execution_requirement(self) -> None:
        from tcms_ai_testgen.models import GenRequest

        req = GenRequest(target="EBM", requirements=["超速触发制动"], num_cases=3)
        u = build_user_prompt(req)
        assert "execution" in u
        assert "3 条" in u
