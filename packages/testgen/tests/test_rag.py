"""rag 检索层测试：金标索引提取/检索质量/容错（离线，不依赖上游）。"""

from __future__ import annotations

import pytest

from tcms_ai_testgen.asset_loader import default_upstream_root
from tcms_ai_testgen.rag import GoldenIndex, _extract_functions

_FAKE_TEST = '''\
"""模块注释。"""

import pytest


def test_door_fault_blocks(bus, db, simulator):
    """车门故障时禁止全关标志。"""
    simulator.set_door_state(1, 2)
    assert True


def test_speed_ok(db):
    data = _encode(db, "VehicleSpeed", SpeedKmh=200.0)
    assert len(data) == 8


def test_plain_no_doc():
    assert 1
'''


class TestExtract:
    def test_parses_functions_with_docstring_and_body(self) -> None:
        cases = _extract_functions(_FAKE_TEST, "fake_test.py")
        assert len(cases) == 3
        door = cases[0]
        assert door.name == "test_door_fault_blocks"
        assert door.params == "bus, db, simulator"
        assert "车门故障" in door.docstring
        assert "set_door_state" in door.body
        speed = cases[1]
        assert speed.params == "db"
        assert speed.body.strip().startswith("data = _encode")

    def test_extract_ignores_non_test_defs(self) -> None:
        cases = _extract_functions("def helper():\n    pass\ndef test_x():\n    assert 1", "f.py")
        assert [c.name for c in cases] == ["test_x"]

    def test_empty_text(self) -> None:
        assert _extract_functions("", "f.py") == []


class TestIndex:
    def _idx(self) -> GoldenIndex:
        return GoldenIndex(_extract_functions(_FAKE_TEST, "fake_test.py"))

    def test_build_and_size(self) -> None:
        idx = self._idx()
        assert idx.size == 3

    def test_retrieve_by_signal(self) -> None:
        idx = self._idx()
        hits = idx.retrieve("door DoorControl Fault")
        assert hits and hits[0].name == "test_door_fault_blocks"

    def test_retrieve_empty_query(self) -> None:
        idx = self._idx()
        assert idx.retrieve("") == []
        assert idx.retrieve("   ") == []

    def test_retrieve_no_match(self) -> None:
        idx = self._idx()
        assert idx.retrieve("zzzqqq_unknown_xxx") == []

    def test_snippet_shape(self) -> None:
        idx = self._idx()
        c = idx.cases[0]
        s = c.snippet()
        assert "test_door_fault_blocks" in s
        assert "车门故障" in s
        assert len(s) <= 450


class TestRealUpstream:
    def test_indexes_real_tests(self) -> None:
        root = default_upstream_root()
        if not (root / "tests").is_dir():
            pytest.skip(f"上游仓库不可用: {root}")
        idx = GoldenIndex.from_tests_dir(root / "tests")
        assert idx.size > 500  # 上游 636 个用例
        hits = idx.retrieve("车门 Door2State Fault", top_k=3)
        assert hits
        assert any("door" in h.name for h in hits)

    def test_missing_dir_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            GoldenIndex.from_tests_dir(tmp_path / "nope")
