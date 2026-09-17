#!/usr/bin/env python3
"""examples/replay_demo.py —— 真实回放链演示：.asc 日志 → 业务逻辑 → 证据断言。

剧情（examples/demo_trip.asc，见 make_demo_asc.py 头注释）:
    1. VCU 心跳中断 400ms → 看门狗判定 vcu 故障
    2. 车速 185km/h 超 EBI(160) → ATP 监督 ebi + 紧急制动触发(overspeed)
    3. 速度归零后 EBM 缓解
    4. 车速 60km/h 时车门误开 → 门-车联锁违规 + 紧急制动触发(door_open)

运行::

    python examples/replay_demo.py            # 断言剧情 + 打印回放报告
    python examples/replay_demo.py --report   # 只打印报告
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcms.replay import (  # noqa: E402
    ALERT_ATP_LEVEL,
    ALERT_EBM_TRIGGER,
    ALERT_WATCHDOG_FAULT,
    ReplayChain,
)

ASC = Path(__file__).resolve().parent / "demo_trip.asc"


def main() -> int:
    report_only = "--report" in sys.argv
    chain = ReplayChain.from_asc(str(ASC))
    report = chain.run()

    kinds = report["alert_kinds"]
    alerts = report["alerts"]

    print("=" * 72)
    print("TCMS 回放链演示：真实 .asc 日志 → 业务逻辑 → 证据断言")
    print("=" * 72)
    print(f"日志帧数        : {report['frames']}")
    print(f"告警类别        : {kinds}")
    print(f"EBM 状态        : {report['ebm_state']}")
    print(f"EBM 是否触发    : {report['ebm_triggered']}")
    print(f"看门狗状态      : {report['watchdog_states']}")
    print(f"ATP 最后等级    : {report['atp_last_level']}")
    print()

    if report_only:
        return 0

    # ---- 证据断言（可作回归门禁） ----

    # 1) 看门狗：心跳中断被检出
    wd_faults = [a for a in alerts if a["kind"] == ALERT_WATCHDOG_FAULT]
    assert wd_faults, "心跳中断未被看门狗检出"
    assert any(a["detail"] == "vcu" for a in wd_faults), f"预期 vcu 故障，实际 {wd_faults}"
    assert wd_faults[0]["ts"] >= 2.0, "vcu 故障应在心跳中断后检出"
    print(f"[1] 看门狗检出 vcu 心跳丢失 @ t={wd_faults[0]['ts']:.2f}s  ✓")

    # 2) 超速 EBI + EBM 触发
    atp_alerts = [a for a in alerts if a["kind"] == ALERT_ATP_LEVEL]
    assert any(a["detail"] == "ebi" for a in atp_alerts), f"预期 ATP ebi，实际 {atp_alerts}"
    overspeed_ebm = [
        a for a in alerts if a["kind"] == ALERT_EBM_TRIGGER and a["detail"] == "overspeed"
    ]
    assert overspeed_ebm, "超速未触发紧急制动"
    print(f"[2] ATP 检出超速 → EBM 触发(overspeed) @ t={overspeed_ebm[0]['ts']:.2f}s  ✓")

    # 3) 超速 EB 停车后可再次触发 → 说明中间缓解发生过（EBM 至少触发两次：
    #    overspeed 后回零 → RELEASED → 门剧情再次 BRAKE）
    assert len([a for a in alerts if a["kind"] == ALERT_EBM_TRIGGER]) >= 2, (
        "预期 EBM 至少触发两次（超速 + 门联锁）"
    )
    print("[3] 超速 EB 停车 → 缓解（RELEASED）→ 后续剧情可再次触发 ✓")

    # 4) 门-车联锁违规 + EBM 触发(door_open)
    door_ebm = [a for a in alerts if a["kind"] == ALERT_EBM_TRIGGER and a["detail"] == "door_open"]
    conflict_alerts = [a for a in alerts if a["detail"] == "door_open_while_moving"]
    assert conflict_alerts, "门-车联锁违规未检出"
    assert door_ebm, "开门行车未触发紧急制动"
    # 5) 触发顺序：先超速（5.0s）后门故（7.0s 起）—— 时间轴证据一致
    assert overspeed_ebm[0]["ts"] < door_ebm[0]["ts"], "触发顺序与日志时间轴矛盾"
    print(f"[4] 门-车联锁违规 → EBM 触发(door_open) @ t={door_ebm[0]['ts']:.2f}s  ✓")
    print(
        f"[5] 证据链时间轴一致：overspeed({overspeed_ebm[0]['ts']:.2f}s) < door_open({door_ebm[0]['ts']:.2f}s)  ✓"
    )

    print()
    print("全部剧情断言通过 —— 回放链可作为回归门禁。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
