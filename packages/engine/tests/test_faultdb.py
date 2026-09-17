"""统一故障字典测试（faultdb.py + faults.yaml）：加载/校验/查询/一致性。

验证：
    - YAML 字典可加载，22 条记录字段齐全、fid/key 唯一
    - 与 faultlevel.FAULTS 双源一致性（重名故障等级一致，faultlevel 键全覆盖）
    - 查询 API：by_key/by_fid/by_level/by_subsystem/by_sil/by_layer
    - 非法输入防御：缺字段/重复键/非法等级/非法 SIL/未知键
"""

import pytest

from tcms import faultdb, faultlevel

DICT = faultdb.load_fault_dictionary()


def test_load_total_and_report():
    report = DICT.report()
    assert report["total"] >= 26
    assert set(report["by_level"]) == {"info", "minor", "major", "critical"}
    assert report["by_level"]["critical"] >= 4


def test_every_entry_has_all_required_fields():
    for e in DICT.all():
        for field in faultdb.REQUIRED_FIELDS:
            assert e[field], f"{e['fid']} 缺字段 {field}"


def test_fid_and_key_unique():
    keys = [e["key"] for e in DICT.all()]
    fids = [e["fid"] for e in DICT.all()]
    assert len(keys) == len(set(keys))
    assert len(fids) == len(set(fids))


def test_alignment_with_faultlevel_faults():
    """faultlevel.FAULTS 的键全部在字典中，且等级一致（双源不漂移）。"""
    assert faultdb.check_faultlevel_alignment() == []
    for key, info in faultlevel.FAULTS.items():
        entry = DICT.by_key(key)
        assert entry["level"] == info["level"], key
        assert entry["action"] == faultlevel.LEVEL_ACTION[info["level"]]


def test_action_matches_level_action_mapping():
    """默认处置动作与 faultlevel 等级→动作映射一致（info→none 等）。"""
    for e in DICT.all():
        if e["key"] in faultlevel.FAULTS:
            assert e["action"] == faultlevel.LEVEL_ACTION[e["level"]]


def test_by_key_and_fid_roundtrip():
    e = DICT.by_key("overspeed")
    assert e["fid"] == "F-TCMS-007"
    assert DICT.by_fid("F-TCMS-007")["key"] == "overspeed"


def test_by_level_counts():
    assert len(DICT.by_level("critical")) >= 4
    assert len(DICT.by_level("info")) >= 2


def test_by_subsystem_network_is_largest():
    net = DICT.by_subsystem("网络")
    assert len(net) >= 9
    assert all(e["subsystem"] == "网络" for e in net)


def test_by_sil_covers_0_to_4():
    for sil in range(5):
        assert DICT.by_sil(sil), f"SIL {sil} 无故障"
    # 非安全功能（SIL 0）与 SIL4 各至少 2 条
    assert len(DICT.by_sil(0)) >= 2
    assert len(DICT.by_sil(4)) >= 2


def test_by_layer_has_all_five_layers():
    assert set(DICT.by_layer("application") and [e["layer"] for e in DICT.all()]) == {
        "application",
        "signal",
        "frame",
        "bus",
        "node",
    }


def test_safety_relevant_faults_have_detect_and_inject():
    """关键安全故障（SIL≥3）必须写明检测手段与注入方法（FMEA 完整性）。"""
    for e in DICT.all():
        if int(e["sil"]) >= 3:
            assert "detect" in e and len(e["detect"]) > 10, e["fid"]
            assert "inject" in e and len(e["inject"]) > 5, e["fid"]


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        DICT.by_key("no_such_fault")


def test_unknown_fid_raises():
    with pytest.raises(KeyError):
        DICT.by_fid("F-TCMS-999")


# ---- 校验防御路径 ----


def _entry(**over):
    base = DICT.all()[0]
    merged = dict(base)
    merged.update(over)
    return merged


def test_validate_rejects_missing_field(tmp_path):
    bad = [_entry(name="")]  # 空 name
    with pytest.raises(faultdb.FaultDictionaryError, match="name"):
        faultdb._validate_entries(bad, "test")


def test_validate_rejects_duplicate_key(tmp_path):
    overspeed = DICT.by_key("overspeed")
    bad = [overspeed, _entry(key="overspeed", fid="F-TCMS-999")]
    with pytest.raises(faultdb.FaultDictionaryError, match="重复"):
        faultdb._validate_entries(bad, "test")


def test_validate_rejects_bad_level():
    bad = [_entry(level="fatal")]
    with pytest.raises(faultdb.FaultDictionaryError, match="非法等级"):
        faultdb._validate_entries(bad, "test")


def test_validate_rejects_bad_sil():
    bad = [_entry(sil="9")]
    with pytest.raises(faultdb.FaultDictionaryError, match="SIL"):
        faultdb._validate_entries(bad, "test")


def test_validate_rejects_level_mismatch_with_faultlevel():
    """双源漂移防御：把 overspeed 等级改成 critical 必须被拒。"""
    bad = [_entry(key="overspeed", level="critical")]
    with pytest.raises(faultdb.FaultDictionaryError, match="不一致"):
        faultdb._validate_entries(bad, "test")


def test_load_rejects_non_dict_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(faultdb.FaultDictionaryError):
        faultdb.load_fault_dictionary(p)


def test_describe_one_liner():
    line = faultdb.describe("eb_failure")
    assert "eb_failure" in line and "紧急制动执行失败" in line and "SIL 4" in line
