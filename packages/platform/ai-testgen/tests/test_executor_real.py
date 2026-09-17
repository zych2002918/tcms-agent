"""P2+ 真实执行器测试：execution DSL 编译（纯离线）+ 真实执行（上游可用时）。

编译断言不依赖上游仓库；真实执行用例在 tcms-can-test 存在时跑
（不存在自动 skip，CI 本仓库不强制依赖上游）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_testgen.execution import ExecutionIntent
from tcms_ai_testgen.executor_real import (
    RealExecResult,
    compile_case,
    run_real,
)
from tcms_ai_testgen.models import GeneratedCase


def _case(name: str, purpose: str, expected: str, execution: dict, **kw) -> GeneratedCase:
    return GeneratedCase(
        name=name,
        purpose=purpose,
        expected=expected,
        execution=ExecutionIntent.model_validate(execution),
        **kw,
    )


class TestCompileEncodeBound:
    def test_reject_speed_over_max(self) -> None:
        c = _case(
            "test_speed_over_max_rejected",
            "车速越界应被编码拒绝",
            "编码抛出 EncodeError",
            {"kind": "encode_bound", "setup": [], "expect": [
                {"op": "expect_encode_error", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.1}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert "pytest.raises(EncodeError)" in src
        assert "SpeedKmh=200.1" in src

    def test_accept_speed_physical_max(self) -> None:
        c = _case(
            "test_speed_max_encodes",
            "物理上限可编码",
            "编码成功且长度 8",
            {"kind": "encode_bound", "setup": [], "expect": [
                {"op": "expect_encode_ok", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.0}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert "assert len(data) == 8" in src
        assert "SpeedKmh=200.0" in src
        # 往返断言（v2：防「返回错误数据但长度不变」变异漏网）
        assert "decode_message" in src
        assert 'decoded["SpeedKmh"]' in src

    def test_unmatched_encode_bound_with_setup_returns_none(self) -> None:
        c = _case(
            "test_bad_bound",
            "编码边界",
            "异常",
            {"kind": "encode_bound", "setup": [{"op": "set_speed", "args": {"kmh": 1}}],
             "expect": [{"op": "expect_encode_error", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.1}}]},
        )
        assert compile_case(c) is None  # encode_bound 不允许 setup


class TestCompileSimulate:
    def test_door_fault(self) -> None:
        c = _case(
            "test_door_fault_blocks",
            "车门故障禁止发车",
            "门状态 Fault",
            {"kind": "simulate_inject",
             "setup": [{"op": "set_door_state", "args": {"index": 1, "state": 2}}],
             "expect": [
                 {"op": "expect_signal", "args": {"message": "DoorControl", "signal": "Door2State", "equals": "Fault"}},
                 {"op": "expect_signal", "args": {"message": "DoorControl", "signal": "AllDoorsClosed", "equals": 0}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert "simulator.set_door_state(1, 2)" in src
        assert '"Fault"' in src  # 枚举文本由 VAL_ 解码
        assert "collect(bus" in src

    def test_heartbeat_loss(self) -> None:
        c = _case(
            "test_heartbeat_loss",
            "心跳丢失应检测",
            "0 帧心跳",
            {"kind": "simulate_inject",
             "setup": [{"op": "stop_message", "args": {"message": "TCMS_Heartbeat"}}],
             "expect": [{"op": "expect_no_frames", "args": {"message": "TCMS_Heartbeat"}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert "simulator.stop_message(proto.TCMS_HEARTBEAT)" in src
        assert "count_frames" in src

    def test_overspeed_simulate(self) -> None:
        c = _case(
            "test_overspeed_alarm",
            "超速触发报警",
            "总线出现超速",
            {"kind": "simulate_inject",
             "setup": [{"op": "set_speed", "args": {"kmh": 165.0}},
                       {"op": "send_alarm", "args": {"code": 1, "level": 2, "Overspeed": True}}],
             "expect": [{"op": "expect_signal", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "equals": 165.0}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert "simulator.set_speed(165.0)" in src
        assert "send_alarm(1, 2, Overspeed=True)" in src

    def test_frames_at_least_branch(self) -> None:
        c = _case(
            "test_speed_still_sent",
            "非心跳报文应持续发送",
            "帧数不低于下限",
            {"kind": "simulate_inject",
             "setup": [{"op": "stop_message", "args": {"message": "TCMS_Heartbeat"}}],
             "expect": [{"op": "expect_frames_at_least", "args": {"message": "VehicleSpeed", "min_frames": 3}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert ">= 3" in src

    def test_simulate_no_assert_returns_none(self) -> None:
        c = _case(
            "test_no_assert",
            "无断言",
            "无",
            {"kind": "simulate_inject",
             "setup": [{"op": "set_speed", "args": {"kmh": 10}}],
             "expect": []},
        )
        assert compile_case(c) is None


class TestCompileFaultScenario:
    def test_overspeed_action(self) -> None:
        c = _case(
            "test_overspeed_derate",
            "超速故障应降级",
            "derate",
            {"kind": "fault_scenario", "node": "vcu", "fault": "overspeed",
             "expect": [{"op": "expect_action", "args": {"fault": "overspeed", "action": "derate"}}]},
        )
        src = compile_case(c)
        assert src is not None
        assert 'faultlevel.action_for("overspeed") == "derate"' in src

    def test_fault_scenario_unknown_action_returns_none(self) -> None:
        c = _case(
            "test_unknown",
            "未知",
            "未知",
            {"kind": "fault_scenario", "fault": "overspeed",
             "expect": [{"op": "expect_signal", "args": {"message": "X", "signal": "Y", "equals": 1}}]},
        )
        assert compile_case(c) is None


class TestCompileMeta:
    def test_no_execution_returns_none(self) -> None:
        c = GeneratedCase(name="test_plain", purpose="自然语言", expected="无 execution")
        assert compile_case(c) is None

    def test_broken_name_not_compiled(self) -> None:
        c = _case(
            "gen_1",
            "车速越界",
            "拒绝",
            {"kind": "encode_bound", "setup": [], "expect": [
                {"op": "expect_encode_error", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.1}}]},
        )
        assert compile_case(c) is None


class TestRealExec:
    """真实执行：上游 tcms-can-test 存在时才跑（成本受控、CI 不依赖）。"""

    def _curated(self) -> list[GeneratedCase]:
        return [
            _case(
                "test_speed_over_max_rejected", "车速越界应被编码拒绝", "编码抛出 EncodeError",
                {"kind": "encode_bound", "setup": [], "expect": [
                    {"op": "expect_encode_error", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.1}}]},
            ),
            _case(
                "test_speed_max_encodes", "物理上限可编码", "编码成功且长度 8",
                {"kind": "encode_bound", "setup": [], "expect": [
                    {"op": "expect_encode_ok", "args": {"message": "VehicleSpeed", "signal": "SpeedKmh", "value": 200.0}}]},
            ),
            _case(
                "test_handle_over_max_rejected", "手柄级位越界应被拒绝", "编码抛出 EncodeError",
                {"kind": "encode_bound", "setup": [], "expect": [
                    {"op": "expect_encode_error", "args": {"message": "TractionBrakeHandle", "signal": "HandlePosition", "value": 17}}]},
            ),
        ]

    def test_real_exec_pass_on_upstream(self) -> None:
        from tcms_ai_testgen.asset_loader import default_upstream_root

        root = default_upstream_root()
        if not (root / "tests" / "conftest.py").is_file():
            pytest.skip(f"上游仓库不可用: {root}")
        res = run_real(self._curated(), root)
        assert isinstance(res, RealExecResult)
        assert res.total == 3
        assert res.compiled == 3
        assert res.passed == 3
        assert res.failed == 0
        assert res.exec_pass_rate == 1.0

    def test_run_real_uncompiled_all(self) -> None:
        # 无一条可编译：不触发上游执行，纯逻辑返回
        cases = [GeneratedCase(name="test_abc", purpose="无关", expected="无关预期")]
        res = run_real(cases, Path("."))
        assert res.total == 1
        assert res.compiled == 0
        assert res.exec_pass_rate == 0.0

    def test_run_real_bad_root_raises(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(FileNotFoundError, match="非 tcms-can-test"):
                run_real(self._curated(), td)


class TestExecutionValidation:
    def test_invalid_op_rejected(self) -> None:
        with pytest.raises(Exception):
            ExecutionIntent.model_validate(
                {"kind": "simulate_inject", "setup": [{"op": "rm_rf", "args": {}}], "expect": []}
            )

    def test_fault_scenario_requires_fault(self) -> None:
        ex = ExecutionIntent.model_validate(
            {"kind": "fault_scenario", "expect": [{"op": "expect_action", "args": {"fault": "overspeed", "action": "derate"}}]}
        )
        assert ex.is_executable() is False  # fault 缺失

    def test_valid_execution_is_executable(self) -> None:
        ex = ExecutionIntent.model_validate(
            {"kind": "simulate_inject",
             "setup": [{"op": "set_door_state", "args": {"index": 1, "state": 2}}],
             "expect": [{"op": "expect_signal", "args": {"message": "DoorControl", "signal": "Door2State", "equals": "Fault"}}]}
        )
        assert ex.is_executable() is True
