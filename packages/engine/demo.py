"""TCMS-CAN-Test 面试演示脚本：跑通"正常 → 超速 → 紧急制动 → 丢报 → 看门狗"全场景。

用法: python demo.py
输出为纯文本，可直接展示或粘贴到面试材料中。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import can

from tcms import ebm
from tcms import protocol as proto
from tcms.multinode import MultiNodeSimulator
from tcms.parser import count_frames
from tcms.watchdogs import NodeHealthTable


def banner(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def ok(checks: list[str], cond: bool, label: str) -> None:
    """记录断言：失败立即退出（演示即自证）。"""
    assert cond, f"[自证失败] {label}"
    checks.append(label)
    print(f"    ✓ {label}")


def main() -> None:
    checks: list[str] = []
    bus = can.Bus(interface="virtual", channel="tcms-demo", receive_own_messages=True)
    db = proto.load_database()

    banner("STEP 1 | 启动多节点仿真（VCU / BCU / BMS）")
    sim = MultiNodeSimulator(bus, db)
    sim.start()
    time.sleep(0.8)
    print(f"总线活跃节点: {sim.active_nodes}")
    while bus.recv(timeout=0.01) is not None:
        pass  # 清空启动期积压帧，确保统计窗口只含新建帧
    print(f"1 秒内心跳帧数: {count_frames(bus, 1.0, proto.TCMS_HEARTBEAT)} (期望≈10)")
    print(
        f"1 秒内手柄帧数: {count_frames(bus, 1.0, proto.TRACTION_BRAKE_HANDLE)} (期望≈20，多节点调度下 15-20 属正常)"
    )
    ok(checks, len(sim.active_nodes) >= 3, "多节点仿真启动（≥3 节点活跃）")

    banner("STEP 2 | 超速场景（>160km/h → 超速报警 → 紧急制动判定）")
    sim.send_alarm(1, 2, Overspeed=True)
    time.sleep(0.1)
    n = count_frames(bus, 0.3, proto.ALARM_EVENT)
    print(f"超速报警帧收到 {n} 条")
    ok(checks, n >= 1, "超速报警帧已发出")

    banner("STEP 3 | 节点失活（模拟 BMS 断电）")
    sim.disable_node("BMS")
    time.sleep(0.6)
    n = count_frames(bus, 0.5, proto.ENERGY_STATUS)
    print(f"BMS 失活后能源报文帧数: {n} (期望 0)")
    print(f"其余节点仍活跃: {sim.active_nodes}")
    ok(checks, n == 0 and "BMS" not in sim.active_nodes, "BMS 失活后能源报文停发")

    banner("STEP 4 | 心跳看门狗（3 周期未收心跳 → 节点判离线）")
    table = NodeHealthTable(cycle_time=0.1, miss_threshold=3)
    for _ in range(20):
        msg = bus.recv(timeout=0.05)
        if msg is not None and msg.arbitration_id == proto.TCMS_HEARTBEAT:
            table.feed("VCU")
    table.evaluate()
    print(f"喂心跳后 VCU 状态: {table.status('VCU')} (期望 online)")
    ok(checks, table.status("VCU") == "online", "喂心跳后 VCU 在线")
    sim.disable_node("VCU")
    time.sleep(0.5)
    table.evaluate()
    print(f"心跳停止后 VCU 状态: {table.status('VCU')} (期望 fault)")
    ok(checks, table.status("VCU") == "fault", "心跳停止后 VCU 判故障")

    banner("STEP 5 | 节点恢复（重新上电 → 2 次有效心跳回在线）")
    sim.enable_node("VCU")
    time.sleep(0.3)
    while bus.recv(timeout=0.01) is not None:
        pass
    feeds = 0
    for _ in range(10):
        msg = bus.recv(timeout=0.1)
        if msg is not None and msg.arbitration_id == proto.TCMS_HEARTBEAT:
            table.feed("VCU")
            feeds += 1
    table.evaluate()
    print(f"恢复后收到 {feeds} 次心跳，VCU 状态: {table.status('VCU')} (期望 online)")
    ok(checks, table.status("VCU") == "online", "恢复供电后 VCU 回在线")

    sim.stop()
    bus.shutdown()

    banner("STEP 6 | 紧急制动管理（EBM：模式×原因矩阵 + 缓解/复位闭环）")
    mgr = ebm.EmergencyBrakeManager(mode=ebm.MODE_FAM)
    print(f"初始: 模式={mgr.mode}, 状态={mgr.state}")
    r = mgr.trigger("ato_fault")
    print(
        f"ATO 故障 → 处置={r['action']} (SIL{r['sil']}), 状态={mgr.state}, 自动降级模式={mgr.mode}"
    )
    ok(checks, mgr.state == ebm.STATE_BRAKE, "ATO 故障触发紧急制动")
    vote = mgr.channel_vote("overspeed", channel_a=True, channel_b=False)
    print(f"SIL4 超速双通道表决 (A触发/B正常) → 是否制动: {vote}（任一通道触发即制动，故障安全）")
    ok(checks, vote is True, "SIL4 超速表决：任一通道触发即制动")
    mgr.update_reason_status("ato_fault", False)
    releasable = mgr.release_condition(0.0)
    print(f"列停稳 + ATO 故障消失 → 缓解条件满足={releasable}, 状态={mgr.state}")
    ok(checks, releasable and mgr.state == ebm.STATE_RELEASED, "停车+原因消失 → 缓解")
    mgr.reset()
    print(f"远程复位 → 状态={mgr.state}")
    ok(checks, mgr.state == ebm.STATE_IDLE, "远程复位回 IDLE")

    banner("STEP 7 | CAN 错误状态机 + 事件记录器（统一时间线）")
    from tcms.errstate import CanErrorStateMachine
    from tcms.recorder import (
        EventRecorder,
        RecordedBus,
        hook_ebm,
        hook_errstate,
    )

    # 互操作：错误状态机与 EBM 的动作经 hook 写入同一个事件记录器，
    # 总线帧经 RecordedBus 装饰器透明记录 —— 安全事件与总线流量共享时间线
    rec = EventRecorder(capacity=200)
    ev_bus = can.Bus(interface="virtual", channel="tcms-demo-events", receive_own_messages=True)
    rbus = RecordedBus(ev_bus, rec, node="tcms")
    sm = CanErrorStateMachine()
    hook_errstate(sm, rec, node="elcu")
    hook_ebm(mgr, rec)

    rbus.send(can.Message(arbitration_id=proto.VEHICLE_SPEED, data=bytes(8), is_extended_id=False))
    for _ in range(16):
        sm.tx_error()  # TEC 0→128：error-active → error-passive
    mgr.trigger("overspeed")  # EBM 制动（记录 ebm 事件）
    mgr.update_reason_status("overspeed", False)
    mgr.release_condition(0.0)
    rbus.send(can.Message(arbitration_id=proto.ALARM_EVENT, data=bytes(8), is_extended_id=False))
    rbus.recv(timeout=0.1)  # 回读自己发出的帧 → can_rx 事件

    st = rec.stats()
    print(
        f"时间线事件总数: {st['total']} (can_tx={st['by_type'].get('can_tx', 0)}, "
        f"can_rx={st['by_type'].get('can_rx', 0)}, "
        f"ebm={st['by_type'].get('ebm', 0)}, "
        f"errstate={st['by_type'].get('errstate', 0)})"
    )
    print(
        f"错误状态机: TEC={sm.tec} → 状态 {sm.state} (期望 error-passive)  "
        f"损坏帧统计={sm.error_frames}"
    )
    ok(checks, sm.state == "error-passive", "CAN 错误状态机进入 error-passive")
    print("时间线（前 8 条，ts 为单调时钟秒）:")
    for e in rec.query()[:8]:
        print(f"  [{e['ts']:.3f}] {e['type']:<9} {e['message'] or ''} {e['payload'] or ''}")
    ev_bus.shutdown()

    banner("STEP 8 | 列控执行可信：EBR 硬线回路 + EB 执行反馈 + 网络级指标")
    from tcms.bus import bus_config, is_hardware_configured
    from tcms.busload import BusLoadMonitor
    from tcms.ebr import EbrLoop, EbrLoopPair
    from tcms.exec_feedback import EbExecutionFeedback
    from tcms.schedulability import (
        MessageSpec,
        SchedulabilityAnalyser,
        audit_id_assignment,
    )

    # 8.1 EBR 硬线回路：失电即制动（独立于 CAN 的 SIL4 执行路径）
    loop_a, loop_b = EbrLoop("EBR-A"), EbrLoop("EBR-B")
    pair = EbrLoopPair(loop_a, loop_b)
    loop_a.open_contact("emergency_btn")
    print(f"EBR: 紧急按钮开路 → 回路A失电制动={pair.brake_applied}（2oo2 任一失电即制动）")
    ok(checks, pair.brake_applied, "EBR 按钮开路 → 失电制动")
    loop_a.close_contact("emergency_btn")
    loop_a.break_wire()
    print(
        f"EBR: 回路A断线 → 制动={pair.brake_applied}, 诊断={loop_a.diag_pulse()}, "
        f"降级={pair.degraded}（单断线不损失制动能力）"
    )

    # 8.2 EB 执行反馈：压力 + 回执 + 牵引切除三重证据确认
    fb = EbExecutionFeedback(timeout_s=2.0)
    fb.request_eb("overspeed", ts=0.0)
    fb.on_pressure(350.0, ts=0.1)
    fb.on_eb_active(True, ts=0.2)
    print(f"EB 反馈: 压力+回执就绪 → 状态={fb.state}（还差牵引切除证据）")
    fb.on_traction(False, ts=0.3)
    print(f"EB 反馈: 牵引已切除 → 状态={fb.state}（三重证据齐备=执行确认）")
    ok(checks, fb.state == "APPLIED", "EB 执行三重证据齐备 → APPLIED")
    fb.on_traction(True, ts=0.4)
    print(f"EB 反馈: APPLIED 中牵引恢复 → 状态={fb.state}（联锁违背立即故障）")
    ok(checks, fb.state == "FEEDBACK_FAULT", "EB 执行中牵引恢复 → FEEDBACK_FAULT")
    fb.reset()

    # 8.3 总线负载率 + WCRT 可调度性 + ID 分配审计
    mon = BusLoadMonitor(bitrate=250_000, window_s=1.0)
    for i in range(1000):
        mon.on_frame(8, ts=i * 0.001)
    print(
        f"负载率: 8字节帧@1ms×1s → {mon.load_pct(ts=1.0):.1f}% "
        f"(位级帧模型含最坏填充位，理论 {100.0 * 135 / (0.001 * 250_000):.1f}%)"
    )
    msgs = [
        MessageSpec(0x100, "TCMS_Heartbeat", 8, 0.1),
        MessageSpec(0x200, "VehicleSpeed", 8, 0.1),
        MessageSpec(0x700, "BrakeSystem", 8, 0.1),
    ]
    rep = SchedulabilityAnalyser(msgs).report()
    print(f"可调度性: 利用率={rep['utilization_pct']}% 全可调度={rep['all_schedulable']}")
    ok(checks, rep["all_schedulable"], "WCRT 可调度性分析通过")
    audit = audit_id_assignment(msgs, safety_names={"BrakeSystem"})
    print(f"ID 分配审计: {audit['reason']}（安全报文应占最低 ID=最高优先级段）")
    # 本演示的报文 ID 0x200(普通) < 0x700(安全制动)：审计应如实检出该设计缺陷
    ok(checks, not audit["ok"], "ID 分配审计检出缺陷（0x200 普通报文占优于 0x700 安全报文）")
    cfg = bus_config(env={})
    print(
        f"硬件接口层: 当前 interface={cfg['interface']}（HIL 用 "
        f"TCMS_BUS_INTERFACE 环境变量切换），硬件已配置={is_hardware_configured(env={})}"
    )

    banner("STEP 9 | 第四轮强化：总线故障/时序质量/故障分级/ATP/NMT/2oo3 表决")
    from tcms.atp import DynamicEbiCurve, SpeedSupervisor
    from tcms.busfault import BusFaultInjector
    from tcms.faultlevel import FaultInjector
    from tcms.interlocks import (
        direction_speed_conflict,
        platform_door_release,
        traction_brake_conflict,
    )
    from tcms.jitter import JitterMonitor
    from tcms.nmt import HeartbeatConsumer, HeartbeatProducer
    from tcms.seqcheck import SequenceChecker
    from tcms.voting import SpeedVoter2oo3

    # 9.1 总线级故障注入：短路 → 全体 Bus-Off → 恢复
    bfi = BusFaultInjector()
    for node in ("VCU", "BCU", "BMS"):
        bfi.add_node(node)
    bfi.inject("short")
    print(f"总线短路 → 全体 Bus-Off: {bfi.bus_off_nodes()}（共享介质：故障影响所有节点）")
    ok(checks, len(bfi.bus_off_nodes()) >= 3, "总线短路 → 全体节点 Bus-Off")
    bfi.recover()
    print(
        f"故障恢复 → 节点状态: {[bfi.status_report()['nodes'][n] for n in ('VCU', 'BCU', 'BMS')]}"
    )

    # 9.2 周期抖动/漂移统计（真实时钟偏差）
    jm = JitterMonitor(nominal_period_s=0.1)
    for i in range(11):
        jm.observe(i * 0.1 + (0.0001 if i % 2 else 0.0))
    s = jm.stats()
    print(
        f"抖动统计: mean={s['mean']:.5f}s σ={s['stdev']:.5f}s "
        f"漂移={jm.drift_ppm():.0f}ppm 告警={jm.drift_alarm()}"
    )

    # 9.3 故障分级模型：注入 → 处置
    fi = FaultInjector()
    fi.inject("door_sensor_noise")
    fi.inject("overspeed")
    print(f"故障分级: {fi.active_faults} → 最严重={fi.worst_level()} 处置={fi.report()['actions']}")
    fi.inject("eb_failure")
    print(f"叠加 EB 执行失败 → 处置升级={fi.report()['actions']}（安全不可妥协）")
    ok(checks, fi.worst_level() == "critical", "叠加 EB 故障 → 严重级升级为 critical")

    # 9.4 ATP 超速监督分层 + 动态 EBI 曲线
    sup = SpeedSupervisor(limit_kmh=160)
    for v in (150.0, 156.0, 159.0, 161.0):
        print(f"ATP 速度监督: {v}km/h → {sup.evaluate(v)}")
    ok(checks, sup.evaluate(161.0) == "ebi", "ATP: 161km/h 超限速 → EBI")
    curve = DynamicEbiCurve(target_speed_kmh=30, current_speed_kmh=120, brake_distance_m=900)
    print(
        f"动态 EBI: 距目标 450m 允许 {curve.allowed_at(450):.0f}km/h, "
        f"80km/h 超速={curve.is_overspeed(80.0, 450)}"
    )
    ok(checks, curve.is_overspeed(80.0, 450), "动态 EBI 曲线判定超速")

    # 9.5 CANopen NMT 心跳（CiA 301）
    prod = HeartbeatProducer(node_id=2)
    cons = HeartbeatConsumer(period_ms=100, timeout_ms=300)
    t = 0.0
    cons.on_heartbeat(prod.heartbeat_payload(), t)  # boot-up
    for _ in range(3):
        t += 0.1
        cons.on_heartbeat(prod.heartbeat_payload(), t)
    t += 0.35  # 停止心跳
    nmt_state = cons.check_timeout(t)
    print(f"NMT: 心跳停止 {t * 1000:.0f}ms → 节点状态={nmt_state}（3 周期超时判丢失）")
    ok(checks, nmt_state == "heartbeat_lost", "NMT 心跳超时判定丢失")

    # 9.6 报文序列/时序违规检测
    ck = SequenceChecker(period_s=0.1)
    for i, (seq, ts) in enumerate(
        [
            (5, 0.0),
            (6, 0.1),
            (9, 0.2),  # 乱序：跳过 7/8
            (9, 0.22),  # 重复：0.02s 内同序号
            (12, 0.3),
        ]
    ):  # 乱序：跳过 10/11
        ck.on_frame(0x100, seq, ts)
    print(
        f"序列检查: 乱序={ck.violations['out_of_order']} 重复={ck.violations['duplicate_frame']} "
        f"迟到={ck.violations['late_frame']}"
    )
    ok(
        checks,
        ck.violations["out_of_order"] > 0 and ck.violations["duplicate_frame"] > 0,
        "报文序列检查检出乱序与重复",
    )

    # 9.7 2oo3 速度表决 + 新增联锁
    voter = SpeedVoter2oo3()
    ok_res, v, _ = voter.vote([80.0, 200.0, 80.5])
    print(f"2oo3 表决: [80, 200, 80.5] → 有效={ok_res} 表决速度={v}km/h（多数一致，剔除噪声）")
    ok(checks, ok_res and abs(v - 80.25) < 1e-6, "2oo3 表决剔除噪声输出多数速度")
    tc, _ = traction_brake_conflict(handle_position=5, brake_request=True)
    print(f"牵引-制动互锁: 手柄5+制动请求 → 冲突={tc}")
    ok(checks, tc, "牵引-制动互锁检出冲突")
    dsc, _ = direction_speed_conflict(direction=0, speed_kmh=80.0)
    print(f"方向-速度联动: 中立方向+80km/h → 违规={dsc}")
    ok(checks, dsc, "方向-速度联动检出违规")
    pdr, _ = platform_door_release(speed_kmh=80.0, platform_aligned=True)
    print(f"车门-站台联动: 移动中对准 → 释放违规={pdr}")
    ok(checks, pdr, "车门-站台联动检出违规")

    banner("DONE | 全场景演示完成")
    print(f"自证断言: {len(checks)} 项全部通过")
    for c in checks:
        print(f"  ✓ {c}")
    print("代码: https://github.com/zych2002918/tcms-can-test")


if __name__ == "__main__":
    main()
