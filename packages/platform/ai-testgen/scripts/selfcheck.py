"""自检：核心链路快速验证（离线优先；上游可用时加验真实执行）。

CI / 面试演示共用：
    1) 导入 + mock 生成 → execution DSL 编译（不依赖上游，全离线）
    2) CLI 导入
    3) （可选）上游 tcms-can-test 可用时：mock 生成 → 真实 pytest 执行
任何一步失败 → FAIL，返回非 0。
"""

from __future__ import annotations

import sys


def main() -> int:
    ok = True

    try:
        from tcms_ai_testgen.executor_real import compile_case
        from tcms_ai_testgen.llm import MockLLMClient, extract_json, parse_cases
        from tcms_ai_testgen.models import GenRequest
        from tcms_ai_testgen.oracle import oracle_summary

        req = GenRequest(target="TCMS", requirements=["边界", "故障"], num_cases=12)
        raw = MockLLMClient().generate_cases(req)
        cases, failures = parse_cases(extract_json(raw))
        compiled = sum(1 for c in cases if compile_case(c))
        oracle = oracle_summary()
        print(f"[1/3] 生成/解析 OK: parsed={len(cases)} failures={failures} "
              f"compiled={compiled} oracle_keys={oracle['total']}")
        if not cases or compiled == 0:
            ok = False
    except Exception as exc:  # pragma: no cover
        print(f"[1/3] 生成/解析 FAIL: {exc!r}")
        ok = False

    try:
        from tcms_ai_testgen.cli import main as cli_main  # noqa: F401
        print("[2/3] CLI 导入 OK")
    except Exception as exc:  # pragma: no cover
        print(f"[2/3] CLI FAIL: {exc!r}")
        ok = False

    try:
        from tcms_ai_testgen.asset_loader import default_upstream_root
        from tcms_ai_testgen.executor_real import run_real

        root = default_upstream_root()
        if (root / "tests" / "conftest.py").is_file():
            req = GenRequest(target="TCMS", requirements=["边界"], num_cases=6)
            raw = MockLLMClient().generate_cases(req)
            cases, _ = parse_cases(extract_json(raw))
            res = run_real(cases, root)
            print(f"[3/3] 真实执行 OK: compiled={res.compiled} passed={res.passed} "
                  f"failed={res.failed}（{root}）")
            if res.failed:
                ok = False
        else:
            print(f"[3/3] 真实执行跳过（上游不可用: {root}；设置 TCMS_UPSTREAM_ROOT）")
    except Exception as exc:  # pragma: no cover
        print(f"[3/3] 真实执行 FAIL: {exc!r}")
        ok = False

    print("selfcheck:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
