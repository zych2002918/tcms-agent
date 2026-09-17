"""R1 沙箱写 + 用例真跑：让 Agent **自己写测试并真的跑起来**。

这是"全流程测试工程师"的最后一块拼图。与前几层的分工：

    R0 只读     → 能把语义查清楚
    R2 真实执行 → 能跑**现成**场景验证结论
    R1 沙箱写   → 能**自己造**用例（本模块）
    R2 真实执行 → 把自己造的用例编译成真实 pytest 并跑起来

## 为什么"受约束"是关键（而不是让 LLM 自由发挥）

LLM 写出的用例走的是 testgen 的 **ExecutionIntent DSL**（Pydantic 校验），
再经 `compile_case` 编译成真实 pytest。约束由**编译器**执行，不由提示词祈求：
- 字段缺失 / 类型错 → Pydantic 直接拒绝；
- 未知 op / 未知 kind → 编译期拒绝（工具如实报错并给出可用 op 清单）。

于是"LLM 写测试"这件事有了一道机器闸门：**写得出不等于跑得起来**，
跑不起来就当场诚实失败，Agent 可以据此自己修。

## 反思自愈为什么由 Agent 做，而不是内置循环

原始 testgen 里有一个写死的 `reflect_loop`（失败→分类→带证据重写→重跑）。
这里**不内置**该循环——只提供 `run_draft` 的真实失败输出，让 Agent 自己决定
要不要重写、怎么重写。这正是"Agent"与"固定流水线"的分野：
流水线只能按预设分支走，Agent 可以自己判断并改变策略。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..knowledge import KnowledgeContext
from ..permissions import Permission
from .registry import ToolSpec

# ---------------------------------------------------------------------------
# DSL 词表：**从 testgen 直接读取，绝不手抄**
# ---------------------------------------------------------------------------
# 教训：最初这里手抄了一份 op 清单，防漂移测试立刻抓到它与编译器白名单不一致
# （漏了 inject_fault / recover_fault，且 expect_encode_error 同属 setup 与 assert）。
# 手抄的"说明书"会误导模型写出永远编译不过的用例——所以唯一真源是 testgen 自己。


def dsl_vocabulary() -> dict[str, Any]:
    """返回 DSL 的 kind / setup op / assert op 白名单（直接来自 testgen）。"""
    from tcms_ai_testgen.execution import ASSERT_OPS, EXEC_KINDS, SETUP_OPS

    return {
        "kinds": list(EXEC_KINDS),
        "setup_ops": sorted(SETUP_OPS),
        "expect_ops": sorted(ASSERT_OPS),
    }


_DSL_EXAMPLE = {
    "name": "test_door_fault_derate",
    "purpose": "车门故障必须触发降级处置",
    "preconditions": "系统运行于 auto 模式",
    "steps": ["注入车门故障", "查询处置动作", "校验期望"],
    "expected": "door_fault 触发 derate",
    "covers": ["SR-21"],
    "tier": "safety",
    "execution": {
        "kind": "fault_scenario",
        "node": "vcu",
        "fault": "door_fault",
        "setup": [],
        "expect": [{"op": "expect_action", "args": {"fault": "door_fault", "action": "derate"}}],
    },
}


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "").strip(), flags=re.UNICODE).strip("-")
    return (s or "draft")[:60]


# ---------------------------------------------------------------------------
# 沙箱
# ---------------------------------------------------------------------------


@dataclass
class DraftSandbox:
    """草稿沙箱：``<root>/<run_id>/drafts/``。

    R1 的关键承诺是**可丢弃**：草稿只活在沙箱里，不碰仓库、不进正式用例集。
    要变成正式产物必须走 R3（人工审批）。
    """

    root: Path
    run_id: str

    def dir(self) -> Path:
        d = Path(self.root) / self.run_id / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def case_path(self, draft_id: str) -> Path:
        return self.dir() / f"{_slug(draft_id)}.json"

    def src_path(self, draft_id: str) -> Path:
        return self.dir() / f"{_slug(draft_id)}.py"

    def save(self, draft_id: str, case: dict[str, Any], source: str) -> tuple[Path, Path]:
        cp, sp = self.case_path(draft_id), self.src_path(draft_id)
        cp.write_text(json.dumps({"cases": [case]}, ensure_ascii=False, indent=2), encoding="utf-8")
        sp.write_text(source, encoding="utf-8")
        return cp, sp

    def load_case(self, draft_id: str) -> dict[str, Any] | None:
        p = self.case_path(draft_id)
        if not p.is_file():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        cases = payload.get("cases") if isinstance(payload, dict) else None
        return cases[0] if cases else None

    def list_drafts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.dir().glob("*.json")):
            case = self.load_case(p.stem) or {}
            out.append(
                {
                    "draft_id": p.stem,
                    "name": case.get("name", ""),
                    "purpose": case.get("purpose", ""),
                    "kind": ((case.get("execution") or {}) or {}).get("kind", ""),
                    "compiled": self.src_path(p.stem).is_file(),
                }
            )
        return out


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

_DRAFT_PARAMS = {
    "type": "object",
    "properties": {
        "case": {
            "type": "object",
            "description": (
                "一条受约束的测试用例（testgen DSL）。必填 name/purpose/expected/execution；"
                "execution 形如 {kind, setup[], expect[], node?, fault?}。"
                "先调 dsl_reference 拿可用 op 清单，避免写出编译不过的用例。"
            ),
        },
        "draft_id": {
            "type": "string",
            "description": "草稿 id（修订同一用例时传入相同 id 即可覆盖；缺省由 name 派生）",
        },
    },
    "required": ["case"],
}

_RUN_DRAFT_PARAMS = {
    "type": "object",
    "properties": {
        "draft_id": {"type": "string", "description": "要运行的草稿 id（先 draft_test_case 生成）"}
    },
    "required": ["draft_id"],
}


def _dsl_reference() -> dict:
    """返回 DSL 词表与完整示例（只读，供模型写出合法用例）。"""
    return {
        **dsl_vocabulary(),
        "example": _DSL_EXAMPLE,
        "rules": [
            "execution.kind 必须是 kinds 之一；setup/expect 里的 op 必须是上面列出的值",
            "expect_action 的 args 形如 {fault, action}，用于断言某故障的处置动作",
            "expect_signal 的 args 形如 {message, signal, equals}（枚举信号用文本值断言）",
            "covers 填真实需求 id（如 SR-21），可先用 list_requirements 查",
            "编译由机器执行：op 写错会当场被拒（Pydantic 白名单），不会静默通过",
        ],
        "source": "testgen ExecutionIntent DSL（packages/testgen；白名单直接读取，不手抄）",
    }


def _draft_test_case(
    ctx: KnowledgeContext,  # noqa: ARG001 - 保留参数以便后续做资产交叉校验
    sandbox: DraftSandbox,
    args: dict[str, Any],
) -> dict:
    """校验 + 编译 + 落盘一条用例草稿。编译不过就当场拒（不静默通过）。"""
    raw = args.get("case")
    if not isinstance(raw, dict):
        return {"error": "draft_test_case 需要 case 对象（testgen DSL）"}

    from tcms_ai_testgen.executor_real import compile_case
    from tcms_ai_testgen.models import GeneratedCase

    try:
        case = GeneratedCase.model_validate(raw)
    except Exception as e:  # noqa: BLE001 - Pydantic 校验失败就是 DSL 不合法
        return {
            "error": f"用例不符合 DSL 约束（Pydantic 校验失败）: {type(e).__name__}",
            "detail": str(e)[:800],
            "hint": "调 dsl_reference 看必填字段与可用 op",
        }

    try:
        source = compile_case(case)
    except Exception as e:  # noqa: BLE001 - 编译期异常同样是"不合法"
        v = dsl_vocabulary()
        return {
            "error": f"DSL 编译失败（op/kind 不受支持）: {type(e).__name__}: {e}",
            "hint": f"可用 kind={v['kinds']} setup={v['setup_ops']} expect={v['expect_ops']}",
        }
    if not source:
        return {
            "error": "DSL 编译返回空（execution 缺失或 kind 不受支持）",
            "hint": f"kind 必须是 {dsl_vocabulary()['kinds']} 之一，且必须给出 execution",
        }

    draft_id = str(args.get("draft_id") or "").strip() or _slug(case.name)

    # 让草稿里的资产引用也进入引用校验链：Agent 自己写的测试若引用了不存在的
    # 需求/故障键，会被 verify 节点当成幻觉引用抓出来（而不是"写出来就算数"）。
    refs: list[str] = []
    execu = case.execution
    if execu is not None:
        if execu.fault:
            refs.append(f"fault:{execu.fault}")
        for step in list(execu.expect or []):
            args_d = getattr(step, "args", None) or {}
            fk = args_d.get("fault") if isinstance(args_d, dict) else None
            if fk:
                refs.append(f"fault:{fk}")
    refs += [f"req:{c}" for c in (case.covers or []) if c]

    cp, sp = sandbox.save(draft_id, case.model_dump(), source)
    return {
        "draft_id": draft_id,
        "compiled": True,
        "case_file": cp.name,
        "source_file": sp.name,
        "refs": list(dict.fromkeys(refs)) or None,
        "source_preview": source[:400],
        "note": "草稿在沙箱内；用 run_draft 真跑验证",
    }


def _run_cases_subprocess(cases_path: Path, *, upstream: Path | None, timeout_s: float) -> dict:
    """在独立子进程里跑生成用例（与 run_scenario 同一套真超时机制）。"""
    import os

    env = os.environ.copy()
    if upstream is not None:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{upstream}{os.pathsep}{existing}" if existing else str(upstream)
        # 同时用环境变量显式告知（testgen 的 default_upstream_root 以它为最高优先级），
        # 避免子进程再做一次路径猜测而与主进程不一致
        env["TCMS_UPSTREAM_ROOT"] = str(upstream)
        env["TCMS_UPSTREAM_DIR"] = str(upstream)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - 固定参数列表，无 shell 注入面
            [sys.executable, "-m", "tcms_agent._exec_worker", "cases", str(cases_path)],
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
            "error": f"用例执行超时（>{timeout_s}s，子进程已强制终止）",
            "duration_s": round(time.perf_counter() - started, 3),
        }
    duration = round(time.perf_counter() - started, 3)
    raw = (proc.stdout or "").strip()
    if not raw:
        return {
            "ok": False,
            "error": f"子进程无输出（exit={proc.returncode}）: {(proc.stderr or '')[:300]}",
            "duration_s": duration,
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"输出非 JSON: {raw[:300]}", "duration_s": duration}
    payload["duration_s"] = duration
    return payload


def _failure_lines(stdout: str, limit: int = 12) -> list[str]:
    """从 pytest 输出里挑出对修复最有用的行（不把整段 traceback 灌给模型）。"""
    keep = []
    for ln in (stdout or "").splitlines():
        if any(k in ln for k in ("Error", "assert", "FAILED", "E   ", "Failed")):
            keep.append(ln.rstrip())
    return keep[:limit]


def _run_draft(
    sandbox: DraftSandbox,
    args: dict[str, Any],
    *,
    upstream: Path | None,
    timeout_s: float,
) -> dict:
    """把草稿编译成真实 pytest 并在上游引擎上真跑。"""
    draft_id = str(args.get("draft_id") or "").strip()
    if not draft_id:
        return {"error": "run_draft 需要 draft_id"}
    case = sandbox.load_case(draft_id)
    if case is None:
        return {"error": f"草稿不存在: {draft_id}", "available": [d["draft_id"] for d in sandbox.list_drafts()]}
    if upstream is None:
        return {"error": "缺少上游引擎目录，无法执行用例"}

    res = _run_cases_subprocess(sandbox.case_path(draft_id), upstream=upstream, timeout_s=timeout_s)
    if not res.get("ok"):
        return {"error": res.get("error", "执行失败"), "timed_out": res.get("timed_out", False), "draft_id": draft_id}

    r = res.get("result") or {}
    passed = int(r.get("passed") or 0)
    failed = int(r.get("failed") or 0)
    out = {
        "draft_id": draft_id,
        "total": r.get("total"),
        "passed": passed,
        "failed": failed,
        "all_passed": bool(r.get("total")) and failed == 0,
        "exec_pass_rate": r.get("exec_pass_rate"),
        "duration_s": res.get("duration_s"),
        "engine": "testgen executor_real（编译为真实 pytest 后执行）",
    }
    if failed:
        # 失败输出是 Agent 自我修复的**关键观察**：给要点，不给整段日志
        out["failure_lines"] = _failure_lines(str(r.get("stdout") or ""))
        out["stdout_tail"] = (str(r.get("stdout") or ""))[-1500:]
        out["hint"] = "可修改 case 后用同一 draft_id 重新 draft_test_case 覆盖，再 run_draft"
    return out


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_sandbox_tools(
    ctx: KnowledgeContext,
    sandbox: DraftSandbox,
    *,
    upstream: Path | None,
    timeout_s: float = 120.0,
) -> list[ToolSpec]:
    """构建 R1 沙箱写工具 + 配套的 DSL 参考与真跑工具。"""

    def _dsl(args: dict) -> dict:  # noqa: ARG001
        return _dsl_reference()

    def _draft(args: dict) -> dict:
        return _draft_test_case(ctx, sandbox, args)

    def _list(args: dict) -> dict:  # noqa: ARG001
        drafts = sandbox.list_drafts()
        return {"count": len(drafts), "drafts": drafts, "sandbox": str(sandbox.dir())}

    def _run(args: dict) -> dict:
        return _run_draft(sandbox, args, upstream=upstream, timeout_s=timeout_s)

    return [
        ToolSpec(
            name="dsl_reference",
            description=(
                "查测试用例 DSL 的可用 kind / setup op / expect op 与完整示例。"
                "**写用例前先调它**——op 写错会被编译期当场拒绝。"
            ),
            level=Permission.READ,
            parameters={"type": "object", "properties": {}},
            func=_dsl,
        ),
        ToolSpec(
            name="list_drafts",
            description="列出沙箱里已有的用例草稿（draft_id / 名称 / 是否已编译）。",
            level=Permission.READ,
            parameters={"type": "object", "properties": {}},
            func=_list,
        ),
        ToolSpec(
            name="draft_test_case",
            description=(
                "把一条受约束的测试用例（testgen DSL）写入沙箱：会先经 Pydantic + 编译期校验，"
                "通过后同时落盘用例 JSON 与编译出的 pytest 源码，返回 draft_id。"
                "编译不过会如实报错（附可用 op 清单）。**只写沙箱，不动仓库。**"
            ),
            level=Permission.SANDBOX,
            parameters=_DRAFT_PARAMS,
            func=_draft,
        ),
        ToolSpec(
            name="run_draft",
            description=(
                "把草稿编译成真实 pytest 并在上游 tcms 引擎上执行，返回通过/失败与失败要点。"
                "失败时返回 failure_lines 供你据此修改用例后重试（同一 draft_id 覆盖）。"
            ),
            level=Permission.EXECUTE,
            parameters=_RUN_DRAFT_PARAMS,
            func=_run,
        ),
    ]


__all__ = [
    "DraftSandbox",
    "build_sandbox_tools",
    "dsl_vocabulary",
]
