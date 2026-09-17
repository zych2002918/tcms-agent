"""R2 真执行工具：让 Agent 的结论由**真实引擎**背书，而不是知识库转述。

这一层是整个项目的分水岭：
- R0 只读工具能让 Agent"说得对"（引用真实资产）；
- R2 执行工具才能让 Agent"验得真"——它跑的是上游 tcms 引擎的真实断言。

设计要点：

1. **真超时**：场景在**独立子进程**里跑（`_exec_worker`），超时可真杀。
   线程超时只是"放弃等待"，底层仿真还在跑——那是假超时，会让结论不可信。

2. **产物归档**：每次执行的原始报告落盘到 `沙箱目录/<run_id>/`，
   工具结果里回传归档路径。结论可事后复核，而不是"跑完就没了"。

3. **诚实失败**：场景不存在 / 引擎异常 / 超时，一律结构化 error，
   绝不用"看起来成功"的结果糊弄过去。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..knowledge import KnowledgeContext
from ..permissions import Permission
from .registry import ToolSpec

DEFAULT_TIMEOUT_S = 60.0


# ---------------------------------------------------------------------------
# 沙箱（执行产物归档）
# ---------------------------------------------------------------------------


@dataclass
class ExecutionSandbox:
    """执行产物的归档目录：``<root>/<run_id>/``。

    与"沙箱写"（R1）的区别：这里只归档**执行产物**（引擎返回的原始报告），
    不改动仓库内容。真正会写仓库的 R3 工具必须过人工审批（见 M3-审批）。
    """

    root: Path
    run_id: str
    max_artifacts: int = 200
    _count: int = 0

    def dir(self) -> Path:
        d = Path(self.root) / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def archive(self, name: str, payload: dict[str, Any]) -> str:
        """归档一份产物，返回**相对沙箱根**的路径（便于跨机器复现）。"""
        self._count += 1
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80]
        path = self.dir() / f"{self._count:03d}-{safe}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(path.relative_to(Path(self.root)))


# ---------------------------------------------------------------------------
# 子进程执行
# ---------------------------------------------------------------------------


def _run_scenario_subprocess(
    scenario_path: Path,
    *,
    upstream: Path | None,
    timeout_s: float,
) -> dict[str, Any]:
    """在独立子进程里跑一个场景，返回 {ok, report|error, duration_s, timed_out}。"""
    env = os.environ.copy()
    if upstream is not None:
        # 让子进程能 import 到上游 tcms（与主进程的解析结果保持一致）
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{upstream}{os.pathsep}{existing}" if existing else str(upstream)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - 参数为固定列表，无 shell 注入面
            [sys.executable, "-m", "tcms_agent._exec_worker", str(scenario_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "timed_out": True,
            "error": f"执行超时（>{timeout_s}s，子进程已强制终止）",
            "duration_s": round(time.perf_counter() - started, 3),
        }
    duration = round(time.perf_counter() - started, 3)

    raw = (proc.stdout or "").strip()
    if not raw:
        return {
            "ok": False,
            "timed_out": False,
            "error": f"子进程无输出（exit={proc.returncode}）: {(proc.stderr or '')[:300]}",
            "duration_s": duration,
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "timed_out": False,
            "error": f"子进程输出非 JSON（exit={proc.returncode}）: {raw[:300]}",
            "duration_s": duration,
        }
    payload["duration_s"] = duration
    payload.setdefault("timed_out", False)
    return payload


# ---------------------------------------------------------------------------
# 工具参数 schema
# ---------------------------------------------------------------------------

_RUN_SCENARIO_PARAMS = {
    "type": "object",
    "properties": {
        "scenario_file": {
            "type": "string",
            "description": "场景文件名（先在 scenarios/ 里确认存在，如 door_cascade.yaml）",
        }
    },
    "required": ["scenario_file"],
}

_VERIFY_PARAMS = {
    "type": "object",
    "properties": {
        "fault_key": {"type": "string", "description": "要验证的故障键（真实 faults.yaml 键）"},
        "expected_action": {
            "type": "string",
            "description": "期望处置动作：emergency_brake / derate / shutdown / warning / none",
        },
    },
    "required": ["fault_key", "expected_action"],
}


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


def _resolve_scenario(ctx: KnowledgeContext, file_name: str) -> Path | None:
    """把场景文件名解析为真实路径（必须是资产里的真实场景，防路径穿越）。"""
    hit = next((s for s in ctx.model.scenarios.values() if s.file == file_name), None)
    if hit is None:
        return None
    if ctx.upstream is None:
        return None
    return Path(ctx.upstream) / "scenarios" / file_name


def _run_scenario(ctx: KnowledgeContext, sandbox: ExecutionSandbox, args: dict, timeout_s: float) -> dict:
    name = str(args.get("scenario_file") or "").strip()
    if not name:
        return {"error": "run_scenario 需要非空 scenario_file"}
    # 只允许资产中登记的场景名（白名单，而非拼接路径）——避免任何路径穿越
    path = _resolve_scenario(ctx, name)
    if path is None:
        near = [s.file for s in ctx.model.scenarios.values() if name.split(".")[0] in s.file][:5]
        return {"error": f"场景不存在（非 assets 登记的场景）: {name}", "near_matches": near}
    if not path.is_file():
        return {"error": f"场景文件缺失: {path}"}

    res = _run_scenario_subprocess(path, upstream=ctx.upstream, timeout_s=timeout_s)
    archive = sandbox.archive(f"run-scenario-{name}", {"scenario": name, **res})
    if not res.get("ok"):
        return {"error": res.get("error", "执行失败"), "timed_out": res.get("timed_out", False), "archive": archive}

    report = res.get("report") or {}
    return {
        "scenario": name,
        "all_passed": bool(report.get("all_passed")),
        "passed": report.get("passed"),
        "failed": report.get("failed"),
        "assertions": [
            {
                "fault": a.get("fault"),
                "expected": a.get("expected"),
                "actual": a.get("actual"),
                "passed": a.get("passed"),
            }
            for a in (report.get("assertions") or [])
        ],
        "duration_s": res.get("duration_s"),
        "archive": archive,
        "engine": "tcms.scenarios.run_yaml（真实上游引擎）",
    }


def _verify_fault_action(
    ctx: KnowledgeContext,
    sandbox: ExecutionSandbox,
    args: dict,
    timeout_s: float,
    max_attempts: int = 4,
) -> dict:
    """真执行验证：跑覆盖该故障的场景，用**引擎断言**回答期望处置是否成立。"""
    fault = str(args.get("fault_key") or "").strip()
    expected = str(args.get("expected_action") or "").strip()
    if not fault or not expected:
        return {"error": "verify_fault_action 需要 fault_key 与 expected_action"}
    fd = ctx.model.faults_by_key.get(fault)
    if fd is None:
        return {"error": f"故障键不存在: {fault}"}
    if fd.action != expected:
        # 字典与期望不符：这是"期望本身可能错"的信号，如实指出（不替用户改期望）
        return {
            "verified": False,
            "reason": "期望处置与故障字典不一致（字典才是真源）",
            "dictionary_action": fd.action,
            "requested_action": expected,
            "hint": "若确实要验证字典语义，请把 expected_action 改为字典值",
        }

    covering = [s for s in ctx.model.scenarios.values() if fault in s.fault_keys][:max_attempts]
    if not covering:
        return {"verified": False, "reason": f"没有场景覆盖故障 {fault}", "attempts": []}

    attempts: list[dict] = []
    for s in covering:
        path = Path(ctx.upstream) / "scenarios" / s.file if ctx.upstream else None
        if path is None or not path.is_file():
            attempts.append({"scenario": s.file, "skipped": "场景文件缺失"})
            continue
        res = _run_scenario_subprocess(path, upstream=ctx.upstream, timeout_s=timeout_s)
        archive = sandbox.archive(f"verify-{fault}-{s.file}", {"fault": fault, "scenario": s.file, **res})
        if not res.get("ok"):
            attempts.append({"scenario": s.file, "error": res.get("error"), "timed_out": res.get("timed_out", False), "archive": archive})
            continue
        rel = [a for a in ((res.get("report") or {}).get("assertions") or []) if a.get("fault") == fault]
        attempts.append(
            {
                "scenario": s.file,
                "decisive": bool(rel),
                "assertions": [{"expected": a.get("expected"), "actual": a.get("actual"), "passed": a.get("passed")} for a in rel],
                "archive": archive,
                "duration_s": res.get("duration_s"),
            }
        )
        if rel:
            ok = all(a.get("passed") for a in rel) and all(a.get("actual") == expected for a in rel)
            return {
                "verified": bool(ok),
                "fault": fault,
                "expected_action": expected,
                "dictionary_action": fd.action,
                "scenario": s.file,
                "engine_assertions": [
                    {"expected": a.get("expected"), "actual": a.get("actual"), "passed": a.get("passed")} for a in rel
                ],
                "attempts": attempts,
                "evidence": f"真实引擎在场景 {s.file} 上断言 fault={fault} 的实际处置为 "
                f"{rel[0].get('actual')!r}（期望 {expected!r}，passed={rel[0].get('passed')}）",
                "archive": archive,
                "engine": "tcms.scenarios.run_yaml（真实上游引擎）",
            }
    return {
        "verified": False,
        "fault": fault,
        "expected_action": expected,
        "reason": f"试了 {len(attempts)} 个场景，均未给出该故障的引擎断言",
        "attempts": attempts,
    }


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_execution_tools(
    ctx: KnowledgeContext,
    sandbox: ExecutionSandbox,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[ToolSpec]:
    """构建 R2 真执行工具（需 allow 到 EXECUTE 级别才可用）。"""

    def _rs(args: dict) -> dict:
        return _run_scenario(ctx, sandbox, args, timeout_s)

    def _vf(args: dict) -> dict:
        return _verify_fault_action(ctx, sandbox, args, timeout_s)

    return [
        ToolSpec(
            name="run_scenario",
            description=(
                "在真实 tcms 引擎上执行一个复现场景，返回引擎的真实断言（expected/actual/passed）。"
                "想确认「系统实际怎么动作」时用它——这是唯一能给出真实执行结果的工具。"
            ),
            level=Permission.EXECUTE,
            parameters=_RUN_SCENARIO_PARAMS,
            func=_rs,
        ),
        ToolSpec(
            name="verify_fault_action",
            description=(
                "用真实引擎验证「某故障是否真的触发某处置」：自动挑选覆盖该故障的场景并执行，"
                "依据引擎断言给出 verified 与证据。下结论前应优先用它，而不是只凭故障字典推断。"
            ),
            level=Permission.EXECUTE,
            parameters=_VERIFY_PARAMS,
            func=_vf,
        ),
    ]


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ExecutionSandbox",
    "build_execution_tools",
]
