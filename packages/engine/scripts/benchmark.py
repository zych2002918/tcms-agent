#!/usr/bin/env python3
"""性能基准（benchmark）：把"每秒回放 X 帧 / WCRT 分析 Y ms"变成可追踪数字。

背景：平台化的证据不止覆盖率——性能数字（回放吞吐、可调度性分析耗时、
总线负载计算耗时）是规模化的硬指标。此前这些只能口头说"很快"，
本脚本把它落成机器产物，与 JUnit 趋势并列（Roadmap：性能基准可追踪）。

基准项（无硬件、确定性、秒级完成）：
    1. 回放吞吐：examples/demo_trip.asc（146 帧）→ ReplayChain，
       单位 frames/s（含业务逻辑：联锁/看门狗/ATP/EBM 全链）
    2. WCRT 分析：SchedulabilityAnalyser.analyse_wcrt 对典型报文集，
       报告耗时与可调度结论
    3. 总线负载：BusLoadMonitor 滑动窗口负载率计算耗时

用法::

    python scripts/benchmark.py                # 全部基准 + Markdown 输出
    python scripts/benchmark.py --json out.json  # 落 JSON（供趋势）
    python scripts/benchmark.py --quick        # 仅回放基准（CI smoke）

退出码：0 = 全部完成（不设阈值——基准记录而非门禁）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _bench_replay(path: str) -> dict:
    """回放吞吐基准：完整回放链（.asc → 业务逻辑 → 报告）。"""
    from tcms.replay import ReplayChain

    chain = ReplayChain.from_asc(path)
    t0 = time.perf_counter()
    report = chain.run()
    elapsed = time.perf_counter() - t0
    frames = report["frames"]
    return {
        "name": "replay_throughput",
        "unit": "frames/s",
        "value": round(frames / elapsed, 1),
        "detail": f"{frames} frames in {elapsed * 1000:.1f} ms ({len(report['alerts'])} alerts)",
    }


def _bench_wcrt(n_messages: int = 200) -> dict:
    """WCRT 分析基准：n 条报文的整集可调度性分析耗时（毫秒级）。"""
    from tcms.schedulability import MessageSpec, SchedulabilityAnalyser

    # 构造 n 条周期报文：ID 递增 = 优先级递减；混合 50/100/500ms 周期
    specs = [
        MessageSpec(
            arb_id=0x100 + i,
            name=f"MSG_{i:04d}",
            dlc=8,
            period_s=(0.05 if i % 5 == 0 else 0.1 if i % 3 == 0 else 0.5),
        )
        for i in range(n_messages)
    ]
    analyser = SchedulabilityAnalyser(specs, bitrate=250_000)
    t0 = time.perf_counter()
    results = analyser.analyse_all()
    elapsed = time.perf_counter() - t0
    schedulable = sum(1 for r in results if r.schedulable)
    return {
        "name": f"wcrt_analyse_{n_messages}msgs",
        "unit": "ms",
        "value": round(elapsed * 1000, 2),
        "detail": f"{n_messages} 报文 WCRT 整集分析，{schedulable}/{n_messages} 可调度",
    }


def _bench_busload(duration_s: float = 2.0) -> dict:
    """总线负载滑动窗口计算基准：虚拟总线采集 duration_s 帧并统计。"""
    import can

    from tcms.busload import BusLoadMonitor

    bus = can.Bus(interface="virtual", channel="tcms-bench", receive_own_messages=True)
    mon = BusLoadMonitor(bitrate=250_000)
    from tcms import protocol
    from tcms.simulator import TCMSNodeSimulator

    db = protocol.load_database()
    sim = TCMSNodeSimulator(bus, db)
    sim.start()
    try:
        t_start = time.monotonic()
        n = 0
        while time.monotonic() - t_start < duration_s:
            msg = bus.recv(timeout=0.05)
            if msg is not None:
                mon.on_frame(msg.dlc, time.monotonic() - t_start)
                n += 1
        t0 = time.perf_counter()
        load = mon.load_pct(time.monotonic() - t_start)
        elapsed = time.perf_counter() - t0
    finally:
        sim.stop()
        bus.shutdown()
    return {
        "name": "busload_window",
        "unit": "ms",
        "value": round(elapsed * 1000, 2),
        "detail": f"{n} 帧采集，滑动窗口负载 {load:.1f}%",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="TCMS-CAN-Test 性能基准")
    parser.add_argument("--json", help="JSON 输出路径（默认 stdout）")
    parser.add_argument("--quick", action="store_true", help="仅回放基准（CI smoke）")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    asc = repo / "examples" / "demo_trip.asc"
    if not asc.exists():
        print(f"FAIL 缺少回放样例: {asc}（先跑 examples/make_demo_asc.py）", file=sys.stderr)
        return 1

    results = [_bench_replay(str(asc))]
    if not args.quick:
        results.append(_bench_wcrt())
        results.append(_bench_busload())

    if args.json:
        out = {
            "tool": "tcms-can-test benchmark",
            "version": _version(),
            "results": results,
        }
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {p}")
    else:
        for r in results:
            print(f"{r['name']:24} {r['value']:>10} {r['unit']:10} {r['detail']}")
    return 0


def _version() -> str:
    from tcms import __version__

    return __version__


if __name__ == "__main__":
    raise SystemExit(main())
