"""可调度性分析测试：WCRT 迭代、利用率、ID 分配审计、DBC 实际报文分析。"""

import pytest

from tcms.busload import frame_bits
from tcms.protocol import load_database
from tcms.schedulability import (
    MessageSpec,
    SchedulabilityAnalyser,
    analyse_wcrt,
    audit_id_assignment,
    transmission_time_s,
)

# ---- 传输时间与 WCRT 基础 ----


def test_transmission_time_matches_frame_bits():
    assert transmission_time_s(8, 250_000) == pytest.approx(frame_bits(8) / 250_000)


def test_wcrt_no_interference_equals_own_transmission():
    """无高优先级报文：R = C。"""
    spec = MessageSpec(arb_id=0x100, name="A", dlc=8, period_s=0.1)
    r = analyse_wcrt(spec, [], bitrate=250_000)
    assert r.wcrt_s == pytest.approx(frame_bits(8) / 250_000)
    assert r.schedulable is True
    assert r.iterations == 1


def test_wcrt_interference_from_higher_priority():
    """高优先级 10ms 周期报文干扰：R = C + ⌈(R+τ)/T⌉·C_hp = 2C。"""
    low = MessageSpec(arb_id=0x200, name="Low", dlc=8, period_s=0.1)
    high = MessageSpec(arb_id=0x100, name="High", dlc=8, period_s=0.010)
    r = analyse_wcrt(low, [high], bitrate=250_000)
    c = frame_bits(8) / 250_000
    # 迭代：R=C → ⌈(C+τ)/10ms⌉=1 → R=2C → ⌈(2C+τ)/10ms⌉=1 → 收敛
    assert r.wcrt_s == pytest.approx(2 * c)
    assert r.interference_by["High"] == pytest.approx(c)
    assert r.schedulable is True


def test_wcrt_saturation_makes_unschedulable():
    """高频高优先级流量把低优先级报文 WCRT 推过 deadline。"""
    low = MessageSpec(arb_id=0x200, name="Low", dlc=8, period_s=0.0015)
    high = MessageSpec(arb_id=0x100, name="High", dlc=8, period_s=0.001)
    r = analyse_wcrt(low, [high], bitrate=250_000)
    # 1ms 周期高优先级流：R 收敛到 3C=1.62ms > 1.5ms deadline
    assert r.schedulable is False
    assert r.wcrt_s > r.deadline_s


def test_wcrt_jitter_increases_interference():
    """队列抖动放大高优先级干扰（⌈(R+τ+J)/T⌉）。"""
    low = MessageSpec(arb_id=0x200, name="Low", dlc=8, period_s=0.1)
    high = MessageSpec(arb_id=0x100, name="High", dlc=8, period_s=0.010, jitter_s=0.001)
    r_nojit = analyse_wcrt(low, [MessageSpec(0x100, "High", 8, 0.010)], bitrate=250_000)
    r_jit = analyse_wcrt(low, [high], bitrate=250_000)
    assert r_jit.wcrt_s >= r_nojit.wcrt_s


# ---- 分析器与报告 ----


def test_analyser_requires_positive_period():
    with pytest.raises(ValueError):
        SchedulabilityAnalyser([MessageSpec(0x100, "A", 8, 0.0)])
    with pytest.raises(ValueError):
        SchedulabilityAnalyser([], bitrate=250_000)
    with pytest.raises(ValueError):
        SchedulabilityAnalyser([MessageSpec(0x100, "A", 8, 0.1)], bitrate=0)


def test_analyser_utilization():
    msgs = [
        MessageSpec(0x100, "A", 8, 0.010),
        MessageSpec(0x200, "B", 8, 0.020),
    ]
    an = SchedulabilityAnalyser(msgs, bitrate=250_000)
    expected = sum(frame_bits(8) / p / 250_000 for p in (0.010, 0.020))
    assert an.utilization == pytest.approx(expected)


def test_analyser_report_all_schedulable_for_dbc():
    """用项目真实 DBC + GenMsgSegment 做网段级可调度性分析：每网段全可调度。

    Q2-P-A ③-c 多网段模型：报文按 DBC 属性归属 vehicle/comfort/backbone，
    各网段独立建 analyser（WCRT 只在同网段内计算）——单总线上限叙事解除。
    """
    from tcms.schedulability import analyse_dbc_by_segment

    db = load_database()
    rep = analyse_dbc_by_segment(db, bitrate=250_000)
    assert rep["all_schedulable"] is True
    segs = rep["segments"]
    assert set(segs) == {"vehicle", "comfort", "backbone"}
    # 每网段帧数 = DBC 标注（vehicle 13 / comfort 7 / backbone 1）
    assert segs["vehicle"]["frames"] == 13
    assert segs["comfort"]["frames"] == 7
    assert segs["backbone"]["frames"] == 1
    # 每网段利用率远低于 30%（250kbit/s），不再是单总线 10% 上限叙事
    for name, r in segs.items():
        assert r["all_schedulable"] is True
        assert 0 < r["utilization_pct"] < 30
    assert rep["worst_segment_utilization_pct"] < 30


def test_analyser_report_detects_unschedulable():
    """高频压力流量 + 低优先级 1.5ms 报文 → 报告标记不可调度。"""
    msgs = [
        MessageSpec(0x100, "High", 8, 0.001),
        MessageSpec(0x200, "Low", 8, 0.0015),
    ]
    an = SchedulabilityAnalyser(msgs, bitrate=250_000)
    rep = an.report()
    assert rep["all_schedulable"] is False
    assert any(r["name"] == "Low" for r in rep["unschedulable"])


def test_analyse_all_returns_sorted_by_priority():
    """逐报文结果按优先级（ID 升序）排列。"""
    an = SchedulabilityAnalyser(
        [
            MessageSpec(0x300, "C", 8, 0.1),
            MessageSpec(0x100, "A", 8, 0.1),
            MessageSpec(0x200, "B", 8, 0.1),
        ]
    )
    results = an.analyse_all()
    assert [r.spec.name for r in results] == ["A", "B", "C"]


def test_blocking_source_is_largest_frame():
    """阻塞源 = 传输时间最大者（最坏低优先级阻塞）。"""
    an = SchedulabilityAnalyser(
        [
            MessageSpec(0x100, "A", 2, 0.1),
            MessageSpec(0x200, "B", 8, 0.1),
            MessageSpec(0x300, "C", 4, 0.1),
        ]
    )
    assert an.blocking_source.name == "B"


def test_wcrt_extreme_interference_terminates():
    """极端高频干扰：迭代必终止（deadline 截断/收敛），判定不可调度。"""
    low = MessageSpec(0x200, "Low", 8, 0.001)
    high = MessageSpec(0x100, "High", 8, 0.000001)  # 极端高频
    r = analyse_wcrt(low, [high], bitrate=250_000, max_iterations=50)
    assert not r.schedulable  # 不可能满足 1ms deadline
    assert r.wcrt_s > r.deadline_s


def test_analyser_with_blocking_uses_worst_case():
    """带阻塞口径：低 ID 小帧报文也按全报文集最坏阻塞计算。"""
    an = SchedulabilityAnalyser(
        [
            MessageSpec(0x100, "Tiny", 2, 0.1),  # 传输时间小
            MessageSpec(0x200, "Big", 8, 0.1),  # 传输时间大 → 最坏阻塞源
        ]
    )
    results = an.analyse_all_with_blocking()
    tiny = results[0]
    assert tiny.spec.name == "Tiny"
    assert tiny.blocking_s == transmission_time_s(8, 250_000)  # 用最坏值


# ---- ID 分配审计 ----


def test_audit_safety_ids_lower_than_ordinary():
    """安全报文占据最低 ID（最高优先级）段 → 审计通过。"""
    msgs = [
        MessageSpec(0x100, "BrakeSystem", 8, 0.1),
        MessageSpec(0x200, "Speed", 8, 0.1),
        MessageSpec(0x300, "HB", 8, 0.1),
    ]
    r = audit_id_assignment(msgs, safety_names={"BrakeSystem"})
    assert r["ok"] is True
    assert r["violations"] == []


def test_audit_detects_low_priority_safety_message():
    """安全报文 ID 偏大（优先级低）→ 报告普通报文抢位违规。"""
    msgs = [
        MessageSpec(0x100, "HB", 8, 0.1),
        MessageSpec(0x200, "Speed", 8, 0.1),
        MessageSpec(0x700, "BrakeSystem", 8, 0.1),
    ]
    # 若把 Speed 视为普通报文而 BrakeSystem 是安全报文：
    # 0x200 < 0x700 → 普通报文优先级高于安全报文 → 违规
    r = audit_id_assignment(msgs, safety_names={"BrakeSystem"})
    assert r["ok"] is False
    assert {v["name"] for v in r["violations"]} == {"HB", "Speed"}


def test_audit_incomplete_set_is_ok():
    msgs = [MessageSpec(0x700, "BrakeSystem", 8, 0.1)]
    r = audit_id_assignment(msgs, safety_names={"BrakeSystem"})
    assert r["ok"] is True  # 无法审计时不误报


# ---- DBC 真实风险演示 ----


def test_dbc_id_assignment_real_risk_demo():
    """演示真实 DBC 的 ID 分配：BrakeSystem(0x700) 优先级低于
    VehicleSpeed(0x200)——安全报文未占据最低 ID 段。"""
    db = load_database()
    messages = [
        MessageSpec(
            arb_id=m.frame_id, name=m.name, dlc=m.length, period_s=(m.cycle_time or 100) / 1000.0
        )
        for m in db.messages
    ]
    brake_id = next(m.arb_id for m in messages if m.name == "BrakeSystem")
    speed_id = next(m.arb_id for m in messages if m.name == "VehicleSpeed")
    # 事实陈述：制动系统报文优先级低于车速报文（现实 DBC 如此设计）
    assert brake_id > speed_id
    # 审计工具能识别这个风险点
    r = audit_id_assignment(messages, safety_names={"BrakeSystem"})
    assert r["ok"] is False
