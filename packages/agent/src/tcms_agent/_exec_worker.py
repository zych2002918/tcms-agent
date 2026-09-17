"""子进程场景执行器（被 `tools/execution.py` 以 `python -m` 方式调用）。

**为什么要独立进程**：只有独立进程才能被真正杀死。用线程做超时只能"放弃等待"，
底层仿真还在继续跑——在一个测试平台上，这会让"超时"变成假象，进而让产出的
结论不可信。安全关键域的纪律是：**要么给真实结果，要么给诚实的失败**。

用法：``python -m tcms_agent._exec_worker <scenario.yaml>``
输出：一行 JSON 到 stdout：``{"ok": true, "report": {...}}`` 或 ``{"ok": false, "error": "..."}``
"""

from __future__ import annotations

import json
import sys


def _truncate(obj: object, limit: int = 20000) -> object:
    """把过大的报告截断成可安全回传的形状（保结构、标截断）。"""
    blob = json.dumps(obj, ensure_ascii=False, default=str)
    if len(blob) <= limit:
        return obj
    return {"_truncated": True, "_original_chars": len(blob), "_head": blob[:limit]}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(json.dumps({"ok": False, "error": "用法: python -m tcms_agent._exec_worker <scenario.yaml>"}))
        return 2

    scenario = args[0]
    try:
        import tcms.scenarios as sc  # noqa: PLC0415 - 只有子进程里才需要导入引擎

        report = sc.run_yaml(scenario)
        payload = {"ok": True, "report": _truncate(report)}
    except Exception as e:  # noqa: BLE001 - 任何失败都必须是可回传的诚实结果
        payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
