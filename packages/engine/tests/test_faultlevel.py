"""故障分级模型测试：等级映射/处置策略/合并/注入编排。"""

import pytest

from tcms import faultlevel as fl

LEVEL_INFO, LEVEL_MINOR, LEVEL_MAJOR, LEVEL_CRITICAL = (
    fl.LEVEL_INFO,
    fl.LEVEL_MINOR,
    fl.LEVEL_MAJOR,
    fl.LEVEL_CRITICAL,
)
ACTION_NONE, ACTION_WARNING, ACTION_DERATE, ACTION_EB = (
    fl.ACTION_NONE,
    fl.ACTION_WARNING,
    fl.ACTION_DERATE,
    fl.ACTION_EB,
)


# ---- 分类 ----


def test_classify_known():
    info = fl.classify("overspeed")
    assert info["level"] == LEVEL_MAJOR
    assert "desc" in info


def test_classify_unknown_raises():
    with pytest.raises(ValueError):
        fl.classify("no_such_fault")


def test_all_faults_have_valid_levels():
    for name in fl.FAULTS:
        assert fl.FAULTS[name]["level"] in fl.VALID_LEVELS


# ---- 处置策略 ----


@pytest.mark.parametrize(
    "fault,mode,expected",
    [
        ("soc_low", "auto", ACTION_NONE),  # info → none
        ("temp_high", "auto", ACTION_NONE),
        ("door_sensor_noise", "auto", ACTION_WARNING),  # minor → warning
        ("speed_sensor_drift", "auto", ACTION_WARNING),
        ("door_fault", "auto", ACTION_DERATE),  # major → derate（auto）
        ("door_fault", "cm", ACTION_DERATE),
        ("door_fault", "rm", ACTION_WARNING),  # major → warning（rm 司机处置）
        ("overspeed", "auto", ACTION_DERATE),
        ("eb_failure", "auto", ACTION_EB),  # critical → EB（任何模式）
        ("eb_failure", "rm", ACTION_EB),
        ("pantograph_arc", "auto", ACTION_EB),
        ("traction_brake_conflict", "auto", ACTION_EB),
    ],
)
def test_action_for(fault, mode, expected):
    assert fl.action_for(fault, mode) == expected


# ---- 合并 ----


def test_merge_actions_takes_highest():
    assert fl.merge_actions([ACTION_NONE, ACTION_WARNING]) == ACTION_WARNING
    assert fl.merge_actions([ACTION_WARNING, ACTION_DERATE]) == ACTION_DERATE
    assert fl.merge_actions([ACTION_DERATE, ACTION_EB]) == ACTION_EB
    assert fl.merge_actions([ACTION_NONE]) == ACTION_NONE
    assert fl.merge_actions([]) == ACTION_NONE


# ---- 注入编排器 ----


def test_injector_basic():
    fi = fl.FaultInjector()
    assert fi.active_faults == []
    assert fi.worst_level() == LEVEL_INFO
    assert fi.report()["actions"] == ACTION_NONE

    fi.inject("overspeed")
    assert fi.active_faults == ["overspeed"]
    assert fi.worst_level() == LEVEL_MAJOR
    assert fi.report()["actions"] == ACTION_DERATE


def test_injector_escalation():
    fi = fl.FaultInjector()
    fi.inject("door_sensor_noise")  # minor
    fi.inject("door_fault")  # major
    assert fi.worst_level() == LEVEL_MAJOR
    assert fi.report()["actions"] == ACTION_DERATE

    fi.inject("eb_failure")  # critical
    assert fi.worst_level() == LEVEL_CRITICAL
    assert fi.report()["actions"] == ACTION_EB
    assert set(fi.report()["faults"]) == {"door_sensor_noise", "door_fault", "eb_failure"}


def test_injector_clear():
    fi = fl.FaultInjector()
    fi.inject("overspeed")
    fi.clear("overspeed")
    assert fi.active_faults == []
    assert fi.worst_level() == LEVEL_INFO

    fi.inject("eb_failure")
    fi.inject("overspeed")
    fi.clear_all()
    assert fi.active_faults == []


def test_injector_unknown_raises():
    fi = fl.FaultInjector()
    with pytest.raises(ValueError):
        fi.inject("no_such")
