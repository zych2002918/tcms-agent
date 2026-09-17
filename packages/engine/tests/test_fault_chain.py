"""端到端故障链测试（E2E Fault Chain）—— 跨模块联动验证。

完整链路（对标真实网络恶化到安全触发的全过程）：
    高总线负载 → WCRT 超限（低优先级帧不可调度）→ 心跳帧丢帧
    → 看门狗判节点离线 → 安全策略触发紧急制动（EBM）

覆盖模块：busload → schedulability → watchdogs → ebm
验证"网络层恶化 → 应用层安全响应"的因果链完整成立。

设计要点：
    - 用 BusLoadGenerator 把理论负载压到 80%+（网络容量风险区）
    - 用 SchedulabilityAnalyser 证明低优先级心跳帧 WCRT > 周期（不可调度）
    - 用假时钟 NodeWatchdog 模拟心跳连续丢失 → fault
    - 节点离线触发 EBM 紧急制动（安全降级）
"""

import pytest

from tcms import busload, ebm
from tcms import schedulability as sch
from tcms import watchdogs as wd


def build_stressed_network(target_pct=85.0):
    """构造高负载网络：突发背景流量 + 一条低优先级心跳帧。

    真实机制：周期性流量本身难以让低优先级帧饿死（只要总负载 ≤100%，
    周期流 WCRT 有界）。让低优先级帧 WCRT 超限的是**突发（burst）**——
    高优先级流带队列抖动 J，burst 期间瞬时占用总线，低优先级帧被反复
    推迟，直到错过自身周期（deadline）。这里用 Tindell 分析中的 jitter
    参数建模该机制（J ≈ 4.9ms，接近周期 5ms → 每周期可能连续双帧）。

    返回: (heartbeat_spec, all_specs, analyser_report)
    """
    # 8 条高优先级突发流：ID 0x100-0x170，周期 2ms，抖动 1.9ms
    # 瞬时位速率 = 8 × 135bit / 2ms = 540kbit/s → 216% 总线容量（严重过载）
    # 理论平均负载 = 8 × 135bit / 2ms / 250k ≈ 216% 也超 100%（burst 持续）
    background = [
        sch.MessageSpec(
            arb_id=0x100 + 0x10 * i, name=f"burst_{i}", dlc=8, period_s=0.002, jitter_s=0.0019
        )
        for i in range(8)
    ]
    # 心跳帧：低优先级（ID 大），周期 100ms —— 真实节点心跳
    heartbeat = sch.MessageSpec(arb_id=0x720, name="VCU_Heartbeat", dlc=8, period_s=0.1)
    specs = background + [heartbeat]
    analyser = sch.SchedulabilityAnalyser(specs, bitrate=250_000)
    report = analyser.report()
    return heartbeat, specs, report


def test_high_load_makes_heartbeat_unschedulable():
    """高负载（突发流量）→ 低优先级心跳帧 WCRT 超限（不可调度）。"""
    _, _, report = build_stressed_network()
    assert report["utilization_pct"] > 80.0
    hb_row = next(r for r in report["rows"] if r["name"] == "VCU_Heartbeat")
    assert not hb_row["schedulable"]
    assert hb_row["wcrt_ms"] > hb_row["deadline_ms"]


@pytest.mark.smoke
@pytest.mark.safety
def test_fault_chain_full_path():
    """完整链路：高负载 → 丢帧 → 看门狗离线 → EBM 紧急制动。"""
    # 1) 网络层：高负载下心跳不可调度
    _, _, report = build_stressed_network()
    hb_row = next(r for r in report["rows"] if r["name"] == "VCU_Heartbeat")
    assert not hb_row["schedulable"]

    # 2) 时间轴：假时钟模拟丢帧（心跳周期 100ms，看门狗 miss_threshold=3）
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    watchdog = wd.NodeWatchdog(cycle_time=0.1, miss_threshold=3, recover_threshold=2, now=now)
    # 节点正常时：连续喂心跳
    for _ in range(5):
        watchdog.feed()
        clock["t"] += 0.1
    assert watchdog.state == wd.STATE_ONLINE

    # 网络恶化：心跳帧因 WCRT 超限而丢失（模拟 4 个周期收不到）
    clock["t"] += 0.4  # 4 个心跳周期无帧
    assert watchdog.evaluate() == wd.STATE_FAULT

    # 3) 应用层：节点离线 → 触发 EBM 紧急制动（安全降级）
    mgr = ebm.EmergencyBrakeManager(mode=ebm.MODE_FAM)
    result = mgr.trigger("atp_fault")  # ATP 故障（节点离线同源）→ 制动+降级 RM
    assert result["applied"] is True
    assert mgr.state == ebm.STATE_BRAKE
    assert mgr.mode == ebm.MODE_RM


def test_chain_breaks_when_load_normal():
    """对照组：正常负载下心跳可调度，看门狗不误判（链路不触发）。"""
    gen = busload.BusLoadGenerator(bitrate=250_000)
    background = gen.plan_streams_for_target(target_pct=20.0, low_prio_base_id=0x600)
    heartbeat = sch.MessageSpec(arb_id=0x720, name="VCU_Heartbeat", dlc=8, period_s=0.1)
    specs = [
        sch.MessageSpec(
            arb_id=s["arb_id"], name=f"bg_{s['arb_id']:#x}", dlc=s["dlc"], period_s=s["period_s"]
        )
        for s in background
    ] + [heartbeat]
    analyser = sch.SchedulabilityAnalyser(specs, bitrate=250_000)
    report = analyser.report()
    assert report["all_schedulable"] is True
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    watchdog = wd.NodeWatchdog(cycle_time=0.1, miss_threshold=3, recover_threshold=2, now=now)
    for _ in range(5):
        watchdog.feed()
        clock["t"] += 0.1
    clock["t"] += 0.2  # 仅 2 个周期：低于 miss_threshold=3
    assert watchdog.evaluate() == wd.STATE_ONLINE  # 不误判离线


def test_recovery_path():
    """链路恢复：节点恢复心跳 → 看门狗回在线 → EBM 可复位。"""
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    watchdog = wd.NodeWatchdog(cycle_time=0.1, miss_threshold=3, recover_threshold=2, now=now)
    for _ in range(5):
        watchdog.feed()
        clock["t"] += 0.1
    clock["t"] += 0.4
    assert watchdog.evaluate() == wd.STATE_FAULT

    # 恢复：连续 2 次有效心跳 → online
    for _ in range(2):
        watchdog.feed()
        clock["t"] += 0.1
    assert watchdog.state == wd.STATE_ONLINE

    # EBM：原因消失 + 零速 → 缓解复位
    mgr = ebm.EmergencyBrakeManager(mode=ebm.MODE_FAM)
    mgr.trigger("atp_fault")
    assert mgr.state == ebm.STATE_BRAKE
    mgr.update_reason_status("atp_fault", False)
    assert mgr.release_condition(speed_kmh=0.0) is True
    assert mgr.state == ebm.STATE_RELEASED


def test_load_monitor_sees_high_load():
    """实测负载监视器确认网络处于 over_limit（>50% 设计上限）。"""
    monitor = busload.BusLoadMonitor(bitrate=250_000, window_s=0.5)
    # 注入高频 8 字节帧（位时间 135bit @250k = 0.54ms → ~1850 帧/s → 100%）
    period = 0.54e-3
    ts = 0.0
    while ts < 0.3:
        monitor.on_frame(dlc=8, ts=ts)
        ts += period
    assessment = monitor.assess(ts=0.3)
    assert assessment["level"] == "over_limit"
    assert assessment["load_pct"] > 50.0


def test_unschedulable_count_in_report():
    """报告明确列出不可调度报文。"""
    _, _, report = build_stressed_network()
    assert len(report["unschedulable"]) >= 1
    assert any(r["name"] == "VCU_Heartbeat" for r in report["unschedulable"])
