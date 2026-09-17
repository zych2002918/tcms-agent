"""生成 CAN 活动时序甘特图（时间 × 帧ID/事件泳道）—— 面试演示素材。

用法: python scripts/plot_timeline.py
输出: docs/timeline_demo.png（提交进仓库，README 展示）

可视化口径：
    - 每条泳道 = 一个 CAN 仲裁 ID（帧活动）或一类安全事件（EBM / errstate）
    - 横轴 = 相对时间（单调时钟，首事件归零）
    - 帧用圆点（按 ID 着色），安全事件用三角标记 + 文本标注
    - 一眼看出"错误积累 → 制动触发 → 缓解"的安全事件序列与总线流量的时序关系
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")  # 无头环境（CI/服务器）也可生成
import can
import matplotlib.pyplot as plt

from tcms import ebm
from tcms import protocol as proto
from tcms.errstate import CanErrorStateMachine
from tcms.recorder import EventRecorder, RecordedBus, hook_ebm, hook_errstate

OUT_PATH = os.path.join("docs", "timeline_demo.png")


def setup_cjk_font() -> bool:
    """优先使用中文字体（本地 Windows 渲染中文标题），失败则退回英文标题。"""
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        if name in matplotlib.font_manager.get_font_names():
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return True
    return False


def run_scenario(rec: EventRecorder) -> None:
    """驱动一个可复现的时序场景：周期帧 + 错误积累 + EBM 制动/缓解 + 恢复。"""
    bus = can.Bus(interface="virtual", channel="tcms-plot", receive_own_messages=True)
    rbus = RecordedBus(bus, rec, node="tcms")
    sm = CanErrorStateMachine()
    hook_errstate(sm, rec, node="elcu")
    mgr = ebm.EmergencyBrakeManager()

    # EBR 硬线回路事件（失电制动 / 得电缓解）写入统一时间线
    from tcms.ebr import EbrLoop

    ebr_loop = EbrLoop(name="EBR-A")
    ebr_loop.open_contact("atp_contact")  # ATP 触点断开 → 回路失电 → 制动
    rec.record_event(
        "ebm",
        category="ebr",
        message="ebr_loop_deenergized",
        payload={"brake_applied": True, "open_contacts": list(ebr_loop.open_contacts)},
        ts=time.monotonic(),
    )
    ebr_loop.close_contact("atp_contact")  # 触点恢复 → 回路得电 → 缓解
    rec.record_event(
        "ebm",
        category="ebr",
        message="ebr_loop_energized",
        payload={"brake_applied": False},
        ts=time.monotonic(),
    )

    for i in range(6):
        # 常规流量：心跳 + 速度帧持续发送
        rbus.send(
            can.Message(arbitration_id=proto.TCMS_HEARTBEAT, data=bytes(8), is_extended_id=False)
        )
        rbus.send(
            can.Message(arbitration_id=proto.VEHICLE_SPEED, data=bytes(8), is_extended_id=False)
        )
        if i < 2:
            for _ in range(2):  # 总线干扰：发送错误积累
                sm.tx_error()
        if i == 3:
            hook_ebm(mgr, rec)
            mgr.trigger("overspeed")  # 超速 → 紧急制动
            mgr.update_reason_status("overspeed", False)
            mgr.release_condition(0.0)  # 停稳 → 缓解
        time.sleep(0.06)
    bus.shutdown()


def plot(rec: EventRecorder) -> None:
    events = rec.query()
    t0 = events[0]["ts"]
    ids = sorted({e["arb_id"] for e in events if e["arb_id"] is not None})
    lanes = [f"0x{i:03X}" for i in ids] + ["EBM", "ERRSTATE"]
    idx = {k: i for i, k in enumerate(lanes)}
    cjk = setup_cjk_font()

    fig, ax = plt.subplots(figsize=(11, 5.5))
    colors = plt.cm.tab10(np.linspace(0, 1, len(ids)))
    color_by_id = dict(zip(ids, colors))

    for e in events:
        t = e["ts"] - t0
        if e["arb_id"] is not None:
            ax.scatter(
                t, idx[f"0x{e['arb_id']:03X}"], s=22, zorder=2, color=color_by_id[e["arb_id"]]
            )
        elif e["type"] == "ebm":
            if e.get("category") == "ebr":
                # EBR 硬线回路事件（失电制动/得电缓解）用方块 + 不同色
                ax.scatter(t, idx["EBM"], marker="s", s=70, zorder=3, color="#9467bd")
                ax.annotate(
                    e["message"],
                    (t, idx["EBM"]),
                    textcoords="offset points",
                    xytext=(5, -12),
                    fontsize=6.5,
                    color="#9467bd",
                )
            else:
                ax.scatter(t, idx["EBM"], marker="v", s=90, zorder=3, color="#d62728")
                ax.annotate(
                    e["message"],
                    (t, idx["EBM"]),
                    textcoords="offset points",
                    xytext=(5, 6),
                    fontsize=7,
                    color="#d62728",
                )
        else:  # errstate
            ax.scatter(t, idx["ERRSTATE"], marker="^", s=90, zorder=3, color="#ff7f0e")
            ax.annotate(
                e["message"],
                (t, idx["ERRSTATE"]),
                textcoords="offset points",
                xytext=(5, 6),
                fontsize=7,
                color="#ff7f0e",
            )

    ax.set_yticks(range(len(lanes)), labels=lanes)
    ax.set_ylim(-0.6, len(lanes) - 0.4)
    ax.set_xlabel("相对时间 (s)")
    if cjk:
        ax.set_title("TCMS CAN 总线活动时序图（帧 × 安全事件统一时间线）")
    else:
        ax.set_title("TCMS CAN bus activity timeline (frames x safety events)")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    fig.savefig(OUT_PATH, dpi=150)
    print(f"已生成: {OUT_PATH}（事件 {len(events)} 条，泳道 {len(lanes)} 条）")


if __name__ == "__main__":
    import numpy as np  # 延迟导入，仅画图需要

    rec = EventRecorder(capacity=500)
    run_scenario(rec)
    plot(rec)
