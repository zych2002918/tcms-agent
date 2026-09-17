"""oracle 测试：10 键白名单语义、期望派生、汇总。"""

from __future__ import annotations

from tcms_ai_testgen.oracle import (
    ACTION_DERATE,
    ACTION_EB,
    ACTION_NONE,
    ACTION_WARNING,
    describe,
    fault_keys,
    is_known_fault,
    lookup,
    oracle_summary,
)


class TestOracleLookup:
    def test_ten_keys(self) -> None:
        keys = fault_keys()
        assert len(keys) == 10
        # 全白名单键都已知
        for k in keys:
            assert is_known_fault(k)
            e = lookup(k)
            assert e is not None
            assert e.key == k

    def test_level_and_action(self) -> None:
        e = lookup("overspeed")
        assert e is not None
        assert e.level == "major"
        assert e.action == ACTION_DERATE
        eb = lookup("eb_failure")
        assert eb is not None
        assert eb.level == "critical"
        assert eb.action == ACTION_EB

    def test_unknown_returns_none(self) -> None:
        assert lookup("nonexistent_fault") is None
        assert is_known_fault("nonexistent_fault") is False

    def test_action_domain(self) -> None:
        # 处置动作域：4 种
        actions = {e.action for e in (lookup(k) for k in fault_keys())}
        assert actions == {ACTION_NONE, ACTION_WARNING, ACTION_DERATE, ACTION_EB}

    def test_signals_present_for_door(self) -> None:
        e = lookup("door_fault")
        assert e is not None
        assert ("DoorControl", "Door2State") in e.signals

    def test_describe(self) -> None:
        d = describe("overspeed")
        assert "超速" in d
        assert "derate" in d
        assert "未知" in describe("nope")

    def test_summary(self) -> None:
        s = oracle_summary()
        assert s["total"] == 10
        assert set(s["by_level"]) == {"info", "minor", "major", "critical"}
        assert s["by_level"]["critical"] == 3

    def test_fault_keys_sorted_by_level(self) -> None:
        keys = fault_keys()
        # info/minor 键在 critical 键之前
        idx_critical = [keys.index(k) for k in keys if lookup(k).level == "critical"]
        assert min(idx_critical) > 0  # critical 不排最前（info/minor/major 在前）
