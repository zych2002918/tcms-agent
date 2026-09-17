"""P1 demo：加载真实资产并打印摘要（自测：python examples/demo_assets.py）。

用法：
    python examples/demo_assets.py                 # 默认找 ../tcms-can-test
    TCMS_UPSTREAM_ROOT=D:/x/tcms-can-test python examples/demo_assets.py
"""

from __future__ import annotations

from tcms_ai_testgen.asset_loader import default_upstream_root, load_assets


def main() -> int:
    root = default_upstream_root()
    dbc_dir = root / "tcms"
    scen_dir = root / "scenarios"

    if not dbc_dir.is_dir() or not scen_dir.is_dir():
        print(f"[!] 未找到上游仓库资产目录: {root}")
        print(f"    期望: {dbc_dir} 与 {scen_dir}")
        print("    设置环境变量 TCMS_UPSTREAM_ROOT 指向 tcms-can-test 仓库根目录后重试。")
        return 1

    bundle = load_assets(dbc_dir, scen_dir)
    s = bundle.stats
    print(f"上游仓库: {root}")
    print(f"DBC 文件  : {s.parsed_dbc} 解析成功 / {s.bad_dbc} 坏 (共 {s.dbc_count})")
    print(f"场景 YAML : {s.parsed_yaml} 解析成功 / {s.bad_yaml} 坏 (共 {s.yaml_count})")
    print(f"parse_rate: {s.parse_rate:.1%}  (口径见 docs/metrics.md，分母含坏文件)")
    if bundle.bad_files:
        print("坏文件清单:")
        for b in bundle.bad_files:
            print(f"  - {b}")

    print("\n== DBC 报文清单 ==")
    for d in bundle.dbc:
        print(f"[{d.source_path}] version={d.version or '?'} nodes={d.nodes}")
        for m in d.messages:
            print(f"  BO_ {m.frame_id:<5} {m.name:<22} dlc={m.dlc} tx={m.transmitter:<6} "
                  f"cycle={m.cycle_ms if m.cycle_ms is not None else 'event'} send={m.send_type}")
            for sig in m.signals:
                rng = sig.physical_range
                lo = f"{rng[0]}" if rng[0] is not None else "-inf"
                hi = f"{rng[1]}" if rng[1] is not None else "+inf"
                sign = "-" if sig.is_signed else "+"
                print(f"      SG_ {sig.name:<20} {sig.start_bit}|{sig.length}@{sign} "
                      f"({sig.factor:g},{sig.offset:g}) [{lo}|{hi}] {sig.unit}")

    print("\n== 场景清单 ==")
    for sc in bundle.scenarios:
        inj = len(sc.inject_steps)
        rec = len(sc.recover_steps)
        faults = sorted({st.fault for st in sc.steps if st.fault})
        print(f"  {sc.name or '(未命名)':<14} steps={len(sc.steps)} (inject={inj}, recover={rec}) "
              f"faults={faults}")
        if sc.skipped_steps:
            print(f"      [!] 跳过未识别步骤: {sc.skipped_steps}")

    print(f"\n汇总: {len(bundle.dbc)} DBC, {s.parsed_dbc}/{s.dbc_count} parsed; "
          f"{len(bundle.scenarios)} 场景, {s.parsed_yaml}/{s.yaml_count} parsed")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
