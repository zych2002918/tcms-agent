"""生成 demo 动画 GIF（事件时间线逐帧推进）—— 面试演示素材。

用法: python scripts/make_demo_gif.py
输出: docs/demo_timeline.gif（若 Pillow 可用；否则仅输出 PNG 帧序列）

可视化口径：
    - 复用 plot_timeline 的场景驱动（可复现时序）
    - 把事件按时间排序，逐帧"点亮"当前事件（红色高亮 + 时间轴游标）
    - 合成 GIF 动画（事件推进 → 一眼看到安全事件序列）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tcms.recorder import EventRecorder

OUT_GIF = os.path.join("docs", "demo_timeline.gif")


def setup_cjk_font() -> bool:
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        if name in matplotlib.font_manager.get_font_names():
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return True
    return False


def main() -> int:
    from scripts import plot_timeline as pt

    rec = EventRecorder(capacity=500)
    pt.run_scenario(rec)
    events = rec.query()
    t0 = events[0]["ts"]
    ids = sorted({e["arb_id"] for e in events if e["arb_id"] is not None})
    lanes = [f"0x{i:03X}" for i in ids] + ["EBM", "ERRSTATE"]
    idx = {k: i for i, k in enumerate(lanes)}
    setup_cjk_font()
    colors = plt.cm.tab10(range(len(ids)))
    color_by_id = dict(zip(ids, colors))

    frames = []
    # 前 N 帧：逐事件点亮；之后：定格全部
    n_events = len(events)
    step = max(1, n_events // 24)  # 约 24 帧
    for until in range(0, n_events + 1, step):
        fig, ax = plt.subplots(figsize=(11, 5.5))
        for k, e in enumerate(events[:until]):
            t = e["ts"] - t0
            if e["arb_id"] is not None:
                ax.scatter(t, idx[f"0x{e['arb_id']:03X}"], s=22, color=color_by_id[e["arb_id"]])
            elif e["type"] == "ebm":
                marker = "s" if e.get("category") == "ebr" else "v"
                c = "#9467bd" if e.get("category") == "ebr" else "#d62728"
                ax.scatter(t, idx["EBM"], marker=marker, s=90, color=c)
            else:
                ax.scatter(t, idx["ERRSTATE"], marker="^", s=90, color="#ff7f0e")
        # 当前帧游标
        if until < n_events:
            cur_t = events[until]["ts"] - t0
            ax.axvline(cur_t, color="#333333", lw=1.2, ls="--", alpha=0.6)
            ax.text(
                cur_t,
                len(lanes) + 0.25,
                f"t={cur_t:.2f}s 事件 {until}/{n_events}",
                ha="center",
                fontsize=9,
                color="#333333",
            )
        ax.set_yticks(range(len(lanes)), labels=lanes)
        ax.set_ylim(-0.6, len(lanes) + 0.5)
        ax.set_xlabel("相对时间 (s)")
        ax.set_title("TCMS CAN 总线活动时序（帧 × 安全事件）— demo 动画")
        ax.grid(axis="x", alpha=0.3)
        fig.tight_layout()
        frames.append(fig)

    try:
        import io

        from PIL import Image

        images = []
        for fig in frames:
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110)
            plt.close(fig)
            buf.seek(0)
            images.append(Image.open(buf).convert("RGB"))
        os.makedirs(os.path.dirname(OUT_GIF), exist_ok=True)
        images[0].save(OUT_GIF, save_all=True, append_images=images[1:], duration=200, loop=0)
        print(f"已生成: {OUT_GIF}（{len(images)} 帧）")
        return 0
    except ImportError:
        os.makedirs("docs/gif_frames", exist_ok=True)
        for i, fig in enumerate(frames):
            fig.savefig(f"docs/gif_frames/frame_{i:03d}.png", dpi=110)
            plt.close(fig)
        print("Pillow 未安装：输出帧序列到 docs/gif_frames/（pip install Pillow 后可合成 GIF）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
