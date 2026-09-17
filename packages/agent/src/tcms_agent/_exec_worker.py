"""子进程执行器（被 `tools/execution.py` 与 `tools/sandbox.py` 以 `python -m` 调用）。

**为什么要独立进程**：只有独立进程才能被真正杀死。用线程做超时只能"放弃等待"，
底层仿真还在继续跑——在一个测试平台上，这会让"超时"变成假象，进而让产出的
结论不可信。安全关键域的纪律是：**要么给真实结果，要么给诚实的失败**。

两种模式：
    python -m tcms_agent._exec_worker <scenario.yaml>
        跑一个复现场景 → {"ok": true, "report": {...}}

    python -m tcms_agent._exec_worker cases <cases.json>
        跑一批生成用例（testgen DSL）→ {"ok": true, "result": {...}}

两种模式都只往 stdout 写一行 JSON。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _truncate(obj: object, limit: int = 20000) -> object:
    """把过大的报告截断成可安全回传的形状（保结构、标截断）。"""
    blob = json.dumps(obj, ensure_ascii=False, default=str)
    if len(blob) <= limit:
        return obj
    return {"_truncated": True, "_original_chars": len(blob), "_head": blob[:limit]}


def _run_scenario(path: str) -> dict:
    import tcms.scenarios as sc  # noqa: PLC0415 - 只有子进程里才需要导入引擎

    return {"ok": True, "report": _truncate(sc.run_yaml(path))}


def _run_cases(cases_path: str) -> dict:
    """用 testgen 的真实执行器跑一批生成用例（编译成 pytest 后真跑）。"""
    from tcms_ai_testgen.executor_real import run_real  # noqa: PLC0415
    from tcms_ai_testgen.models import GeneratedCase  # noqa: PLC0415

    payload = json.loads(open(cases_path, encoding="utf-8").read())  # noqa: PTH123, SIM115
    raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
    upstream = (payload or {}).get("upstream") if isinstance(payload, dict) else None
    cases = [GeneratedCase.model_validate(c) for c in (raw_cases or [])]
    if not cases:
        return {"ok": False, "error": "cases 文件里没有用例"}
    if not upstream:
        # 用例文件里没写上游目录 → 用 testgen 的解析链（monorepo 感知，含环境变量）
        from tcms_ai_testgen.asset_loader import default_upstream_root  # noqa: PLC0415

        upstream = default_upstream_root()
    if not upstream or not Path(str(upstream)).is_dir():
        return {"ok": False, "error": f"上游引擎目录不可用: {upstream!r}"}
    res = run_real(cases, upstream, keep_artifacts=True)
    return {
        "ok": True,
        "result": _truncate(
            {
                "total": res.total,
                "passed": res.passed,
                "failed": res.failed,
                "compiled": getattr(res, "compiled", None),
                "compile_rate": getattr(res, "compile_rate", None),
                "exec_pass_rate": getattr(res, "exec_pass_rate", None),
                "report_path": str(getattr(res, "report_path", "") or ""),
                "stdout": (res.stdout or "")[-6000:],
            }
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "用法: python -m tcms_agent._exec_worker <scenario.yaml> | cases <cases.json>",
                }
            )
        )
        return 2

    try:
        if args[0] == "cases":
            if len(args) < 2:
                payload = {"ok": False, "error": "cases 模式需要 <cases.json> 路径"}
            else:
                payload = _run_cases(args[1])
        else:
            payload = _run_scenario(args[0])
    except Exception as e:  # noqa: BLE001 - 任何失败都必须是可回传的诚实结果
        payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
