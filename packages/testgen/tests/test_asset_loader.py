"""P1 真实资产加载器测试：真实 tcms.dbc + 全量场景 + 合成坏文件。

覆盖目标（每新模块必带单测；坏文件路径用 tmp_path 合成，CI 不依赖外部）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_ai_testgen.asset_loader import (
    _parse_dbc_lines,
    _parse_val_pairs,
    _step_from_yaml,
    default_upstream_root,
    load_assets,
    load_dbc,
    load_scenario,
)
from tcms_ai_testgen.asset_models import (
    AssetBundle,
    AssetIndexStats,
    DbcFile,
    DbcSignal,
)

# ---------------------------------------------------------------------------
# 上游引擎资产基线（tcms-can-test 实测快照）
# ---------------------------------------------------------------------------
# 取数来源：tcms-can-test v1.12.0（E:\DSHworkplace\objects\tcms-can-test）
#   tcms/tcms.dbc      -> 22 报文 / 116 信号 / 11 节点
#   scenarios/*.yaml   -> 104 场景
# 说明：can-test 会持续增补资产，本块是"当前应解析到的口径"而非上限。
#       引擎扩资产后只在此单点更新，测试用例按语义断言，不再散落魔法数字。
UPSTREAM_DBC_VERSION = "1.0.0"
UPSTREAM_DBC_NODE_COUNT = 11
UPSTREAM_DBC_MESSAGE_COUNT = 22
UPSTREAM_DBC_SIGNAL_COUNT = 116
UPSTREAM_SCENARIO_COUNT = 104

# 首版 5 节点 / 8 报文：引擎后续只做增补，按子集断言防"增长时静默丢失"
UPSTREAM_DBC_CORE_NODES = ("TCMS", "VCU", "BMS", "BOGIE", "BCU")
UPSTREAM_DBC_CORE_MESSAGES = frozenset({
    "TCMS_Heartbeat",
    "VehicleSpeed",
    "TractionBrakeHandle",
    "DoorControl",
    "AlarmEvent",
    "PantographStatus",
    "BrakeSystem",
    "EnergyStatus",
})

# ---------------------------------------------------------------------------
# 真实资产定位（与 examples/demo_assets.py 同一策略；找不到则 skip，不硬崩）
# ---------------------------------------------------------------------------


def _real_repo_root() -> Path:
    """定位 tcms-can-test 仓库根目录（只读、纯路径探测）。"""
    root = default_upstream_root()
    if not (root / "tcms" / "tcms.dbc").is_file() or not (root / "scenarios").is_dir():
        pytest.skip(f"真实上游仓库不可用: {root}")
    return root


def _real_dbc_path() -> Path:
    return _real_repo_root() / "tcms" / "tcms.dbc"


# ---------------------------------------------------------------------------
# 真实 DBC 解析
# ---------------------------------------------------------------------------


class TestRealDbc:
    @pytest.fixture
    def dbc(self) -> DbcFile:
        return load_dbc(_real_dbc_path())

    def test_version_and_nodes(self, dbc: DbcFile) -> None:
        assert dbc.version == UPSTREAM_DBC_VERSION
        assert len(dbc.nodes) == UPSTREAM_DBC_NODE_COUNT
        # 首版节点必须仍在（引擎只增不删）
        assert set(UPSTREAM_DBC_CORE_NODES) <= set(dbc.nodes)

    def test_message_inventory(self, dbc: DbcFile) -> None:
        assert dbc.message_count == UPSTREAM_DBC_MESSAGE_COUNT
        names = {m.name for m in dbc.messages}
        # 首版 8 报文必须仍在——防引擎扩报文时静默丢失老报文
        assert UPSTREAM_DBC_CORE_MESSAGES <= names

    def test_signal_total(self, dbc: DbcFile) -> None:
        assert dbc.signal_count == UPSTREAM_DBC_SIGNAL_COUNT
        # 自洽：总数 == 各报文信号数之和（不依赖上游涨落，永久有效）
        assert dbc.signal_count == sum(len(m.signal_names) for m in dbc.messages)
        # 自洽：报文数 == 报文列表长度
        assert dbc.message_count == len(dbc.messages)

    def test_heartbeat_message(self, dbc: DbcFile) -> None:
        m = dbc.find_message("TCMS_Heartbeat")
        assert m is not None
        assert m.frame_id == 256
        assert m.dlc == 8
        assert m.transmitter == "TCMS"
        assert m.signal_names == ["NodeStatus", "RunMode", "HeartbeatCounter"]
        assert m.signal("RunMode").length == 8
        assert m.signal("RunMode").start_bit == 8
        # BA_ 属性回填
        assert m.cycle_ms == 100
        assert m.send_type == "cyclic"

    def test_cycle_time_ba_filled(self, dbc: DbcFile) -> None:
        m = dbc.find_message("AlarmEvent")
        assert m is not None
        assert m.cycle_ms == 0  # GenMsgCycleTime=0（事件型）
        assert m.send_type == "event"

    def test_value_descriptions(self, dbc: DbcFile) -> None:
        vd = dbc.value_descriptions.get(256, {}).get("NodeStatus")
        assert vd is not None
        assert vd[0].label == "PowerOff"
        assert vd[2].label == "Active"
        # 16 位带偏移信号
        b = dbc.find_message("EnergyStatus")
        assert b is not None
        cur = b.signal("BatteryCurrent")
        assert cur is not None
        assert cur.offset == -1000
        assert cur.is_signed is False  # 物理负值由 offset 表达
        assert cur.physical_range == (-1000.0, 1000.0)

    def test_physical_range_fallback_signed(self, dbc: DbcFile) -> None:
        # 无 [min|max] 时按位宽推导（本 DBC 全带范围，构造一个）
        sig = DbcSignal(name="X", start_bit=0, length=8, is_signed=True)
        assert sig.physical_range == (-128.0, 127.0)

    def test_bundle_find_message_missing(self, dbc: DbcFile) -> None:
        # DbcFile 无 find_message——走 AssetBundle
        b = AssetBundle(dbc=[dbc], scenarios=[], stats=AssetIndexStats(yaml_count=0, parsed_yaml=0, bad_yaml=0))
        assert b.find_message("Nope") is None
        assert b.find_message("VehicleSpeed") is not None
        assert b.signal_total == UPSTREAM_DBC_SIGNAL_COUNT

    def test_no_raw_line_errors(self, dbc: DbcFile) -> None:
        assert dbc.raw_line_errors == []

    def test_load_dbc_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_dbc(tmp_path / "nope.dbc")

    def test_load_dbc_bad_encoding(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.dbc"
        bad.write_bytes(b"\xff\xfe\x00\xff")  # 非法 UTF-8
        with pytest.raises(ValueError, match="编码"):
            load_dbc(bad)


# ---------------------------------------------------------------------------
# 真实场景
# ---------------------------------------------------------------------------


class TestRealScenarios:
    @pytest.fixture
    def assets(self) -> AssetBundle:
        root = _real_repo_root()
        return load_assets(root / "tcms", root / "scenarios")

    def test_all_scenarios_parsed(self, assets: AssetBundle) -> None:
        assert assets.stats.parsed_yaml == UPSTREAM_SCENARIO_COUNT
        assert assets.stats.bad_yaml == 0
        assert len(assets.scenarios) == UPSTREAM_SCENARIO_COUNT
        # 自洽：全部 YAML 都解析成功，无遗漏（不依赖上游涨落，永久有效）
        assert assets.stats.parsed_yaml == assets.stats.yaml_count

    def test_scenario_inject_recover_counts(self, assets: AssetBundle) -> None:
        # 按精确名取（"紧急制动" 子串另有多条级联/变体场景，不能用子串匹配）
        eb = assets.find_scenario("紧急制动场景")
        assert eb is not None
        assert len(eb.steps) == 2
        assert len(eb.inject_steps) == 1
        assert len(eb.recover_steps) == 1
        inj = eb.inject_steps[0]
        assert inj.fault == "eb_failure"
        assert inj.node == "vcu"
        assert inj.level == "critical"
        assert inj.expect == "emergency_brake"

    def test_emergency_brake_name_is_ambiguous_by_substring(self, assets: AssetBundle) -> None:
        # 记录口径：含"紧急制动"的场景不止一条，故上面的用例必须按精确名定位
        hits = [s for s in assets.scenarios if "紧急制动" in (s.name or "")]
        assert len(hits) > 1
        assert any(s.name == "紧急制动场景" for s in hits)

    def test_scenario_door_cascade_event_style(self, assets: AssetBundle) -> None:
        # door_cascade 用事件式 action: 写法，应归一化为 inject/recover
        d = [s for s in assets.scenarios if "车门故障级联" in (s.name or "")]
        assert len(d) == 1
        steps = d[0].steps
        assert all(st.kind in ("inject", "recover") for st in steps)
        assert [st.kind for st in steps] == ["inject", "inject", "recover", "recover"]

    def test_scenario_step_at_types(self, assets: AssetBundle) -> None:
        for sc in assets.scenarios:
            for st in sc.steps:
                assert isinstance(st.at, float)
                assert isinstance(st.kind, str)

    def test_all_faults_extracted(self, assets: AssetBundle) -> None:
        faults = sorted({st.fault for sc in assets.scenarios for st in sc.steps if st.fault})
        # 全量场景覆盖的关键故障键（F-TCMS 语义），保证抽取没漏
        for key in ("overspeed", "door_fault", "eb_failure", "crc_error_frame"):
            assert key in faults

    def test_stats_dbc(self, assets: AssetBundle) -> None:
        assert assets.stats.parsed_dbc == 1
        assert assets.stats.bad_dbc == 0
        assert assets.message_total == UPSTREAM_DBC_MESSAGE_COUNT
        assert assets.signal_total == UPSTREAM_DBC_SIGNAL_COUNT

    def test_parse_rate_full(self, assets: AssetBundle) -> None:
        assert assets.stats.parse_rate == pytest.approx(1.0)

    def test_find_scenario_by_name(self, assets: AssetBundle) -> None:
        sc = assets.find_scenario("CRC 错误风暴")
        assert sc is not None
        assert len(sc.inject_steps) == 2


# ---------------------------------------------------------------------------
# 合成坏文件 / 容错
# ---------------------------------------------------------------------------


class TestBadFiles:
    def _write(self, tmp_path: Path, name: str, content: str) -> Path:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_empty_yaml_counted_bad(self, tmp_path: Path) -> None:
        d = tmp_path / "scen"
        d.mkdir()
        self._write(d, "empty.yaml", "")
        b = load_assets(tmp_path / "nope_dbc", d)  # dbc 目录不存在 -> bad_dbc
        assert b.stats.parsed_yaml == 0
        assert b.stats.bad_yaml == 1
        assert b.stats.dbc_count == 0
        assert b.stats.bad_dbc == 1  # 目录缺失按坏 DBC 计 1
        assert len(b.bad_files) == 2

    def test_bad_yaml_syntax(self, tmp_path: Path) -> None:
        d = tmp_path / "scen"
        d.mkdir()
        self._write(d, "bad.yaml", "steps:\n  - at: [1,2\n")  # 语法错
        b = load_assets(tmp_path / "empty_dbc_dir", d)
        assert b.stats.bad_yaml == 1

    def test_scenario_missing_steps(self, tmp_path: Path) -> None:
        d = tmp_path / "scen"
        d.mkdir()
        self._write(d, "nosteps.yaml", "name: 无步骤\n")
        with pytest.raises(ValueError, match="steps"):
            load_scenario(d / "nosteps.yaml")

    def test_load_scenario_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_scenario(tmp_path / "nope.yaml")

    def test_load_scenario_bad_encoding(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_bytes(b"\xff\xfe\x00\xff")
        with pytest.raises(ValueError, match="编码"):
            load_scenario(bad)

    def test_unrecognized_step_skipped_and_recorded(self, tmp_path: Path) -> None:
        d = tmp_path / "scen"
        d.mkdir()
        self._write(
            d,
            "odd.yaml",
            "name: 奇怪场景\nsteps:\n  - at: 1.0\n    inject: {fault: a}\n  - at: 2.0\n    nonsense: 1\n",
        )
        sc = load_scenario(d / "odd.yaml")
        assert len(sc.steps) == 1
        assert sc.skipped_steps != []

    def test_steps_not_list(self, tmp_path: Path) -> None:
        d = tmp_path / "scen"
        d.mkdir()
        self._write(d, "sl.yaml", "name: x\nsteps: 123\n")
        with pytest.raises(ValueError, match="steps 不是列表"):
            load_scenario(d / "sl.yaml")

    def test_empty_dbc_file_parses_empty(self, tmp_path: Path) -> None:
        d = tmp_path / "dbcs"
        d.mkdir()
        self._write(d, "empty.dbc", "")
        f = load_dbc(d / "empty.dbc")
        assert f.message_count == 0
        assert f.signal_count == 0

    def test_dbc_missing_dir_in_assets(self, tmp_path: Path) -> None:
        d = tmp_path / "scen"
        d.mkdir()
        b = load_assets(tmp_path / "missing", d)
        assert b.stats.dbc_count == 0
        assert b.stats.bad_dbc == 1
        assert b.dbc == []
        assert any("目录不存在" in x for x in b.bad_files)


class TestParseHelpers:
    def test_step_from_yaml_nested_inject(self) -> None:
        st = _step_from_yaml({"at": 1.0, "inject": {"node": "bcu", "fault": "crc_error_frame",
                                                    "level": "major", "impact": "x", "expect": "warning"}})
        assert st is not None
        assert st.kind == "inject"
        assert st.fault == "crc_error_frame"
        assert st.node == "bcu"

    def test_step_from_yaml_event_style_action(self) -> None:
        st = _step_from_yaml({"at": 1.0, "action": "recover", "fault": "door_fault"})
        assert st is not None
        assert st.kind == "recover"
        assert st.fault == "door_fault"

    def test_step_from_yaml_bad_at(self) -> None:
        assert _step_from_yaml({"at": "abc"}) is None
        assert _step_from_yaml({"noat": 1}) is None
        assert _step_from_yaml("notdict") is None

    def test_val_pairs_parsing(self) -> None:
        pairs = _parse_val_pairs('VAL_ 256 NodeStatus 0 "PowerOff" 1 "Standby" ;')
        assert set(pairs.keys()) == {0, 1}
        assert pairs[0].label == "PowerOff"

    def test_val_pairs_empty(self) -> None:
        assert _parse_val_pairs("VAL_ 1 X ;") == {}

    def test_dbc_line_errors_recorded(self) -> None:
        f = _parse_dbc_lines(Path("fake.dbc"), ["BO_ 1 X: 8 N", "SG_ bad line", "BO_ abc Y: 8 N"])
        # 第一条 BO_ 有效，坏 SG_（缺括号）记行错，BO_ abc 帧号非整数记行错
        assert len(f.messages) == 1
        assert len(f.raw_line_errors) == 2
        assert f.message_count == 1
