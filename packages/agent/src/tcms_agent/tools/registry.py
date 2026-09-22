"""工具注册表：schema / 权限门禁 / 审计 / 异常兜底。

为什么不用 LangGraph 的 `ToolNode` 直接跑 LangChain 工具：
`ToolNode` 不认识"权限级别"这件事。在安全关键域，**每一次工具调用都必须先过
等级门禁、再留审计记录**，所以执行路径必须由我们自己的注册表收口。
（模型侧的 schema 仍以 OpenAI function-calling 格式暴露，与 LangGraph 完全兼容。）

四条不可让步的纪律：
1. **权限门禁**：超过允许级别的工具既不出现在送给模型的 schema 里，
   也不可在 invoke 时执行（双保险）——而不是"靠 prompt 提醒模型别调"。
2. **诚实错误**：未知工具 / 参数非法 / 执行异常，一律返回结构化 error 结果，
   绝不抛出中断循环，也绝不伪造一个看起来成功的返回。
3. **全量审计**：每次调用记录 名称 / 参数 / 级别 / 耗时 / 成败 / 结果摘要。
4. **结果有界**：送给模型的结果做长度上限截断（保护上下文），
   但审计里保留完整结果摘要的指纹，便于事后核对。
"""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ..permissions import Permission

#: 送给模型的工具结果最大字符数（保护上下文预算）
MAX_RESULT_CHARS = 4000


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明。"""

    name: str
    description: str
    level: Permission
    parameters: dict[str, Any]  # JSON Schema（object）
    func: Callable[[dict[str, Any]], dict[str, Any]]

    def to_openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI function-calling 格式（LangGraph / LangChain 均接受）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCallRecord:
    """一次工具调用的审计记录。"""

    name: str
    args: dict[str, Any]
    level: int
    level_label: str
    ok: bool
    duration_ms: int
    error: str = ""
    result_chars: int = 0
    result_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "level": self.level,
            "level_label": self.level_label,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "result_chars": self.result_chars,
            "result_digest": self.result_digest,
        }


def _digest(payload: Any) -> str:
    """结果指纹（前 12 位 sha256）：用于事后核对"这次调用到底返回了什么"。"""
    try:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - 不可序列化对象退化为 repr
        blob = repr(payload)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _is_error(result: Any) -> str:
    """结果里是否带 error 字段（本项目工具约定：error 用 key 表达）。"""
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])
    return ""


@dataclass
class ToolRegistry:
    """工具注册表 + 执行门禁。"""

    max_level: Permission = Permission.READ
    _specs: dict[str, ToolSpec] = field(default_factory=dict)
    audit: list[ToolCallRecord] = field(default_factory=list)
    max_result_chars: int = MAX_RESULT_CHARS

    # ---- 注册 ----

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise ValueError(f"工具重名（注册表要求唯一）: {spec.name}")
        self._specs[spec.name] = spec
        return spec

    def register_all(self, specs: list[ToolSpec]) -> None:
        for s in specs:
            self.register(s)

    # ---- 查询 ----

    def __len__(self) -> int:
        return len(self._specs)

    def names(self, *, allowed_only: bool = True) -> list[str]:
        """工具名列表；allowed_only=True 时只返回当前级别允许的。"""
        return sorted(
            n for n, s in self._specs.items() if not allowed_only or s.level <= self.max_level
        )

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def schemas(self, *, allowed_only: bool = True) -> list[dict[str, Any]]:
        """送给模型的工具 schema 列表（**只含允许级别**）。"""
        return [
            s.to_openai_schema()
            for s in self._specs.values()
            if not allowed_only or s.level <= self.max_level
        ]

    def describe(self) -> list[dict[str, Any]]:
        """人类可读的工具清单（CLI / 自检 / 文档用）。"""
        return [
            {
                "name": s.name,
                "level": int(s.level),
                "level_label": s.level.label,
                "allowed": s.level <= self.max_level,
                "description": s.description,
            }
            for s in sorted(self._specs.values(), key=lambda x: (x.level, x.name))
        ]

    # ---- 执行 ----

    def invoke(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行一个工具调用，返回结果 dict（含 error 时也是诚实结果）。

        绝不抛异常给调用方——Agent 循环的健壮性依赖"任何工具失败都能变成
        一条可观察的观察结果"。
        """
        args = args if isinstance(args, dict) else {}
        started = time.perf_counter()
        spec = self._specs.get(name)

        if spec is None:
            return self._record_and_return(
                name, args, Permission.READ, False, {"error": f"未注册的工具: {name}"}, started
            )
        if spec.level > self.max_level:
            # 双保险：即便模型绕过 schema 直接点名高权限工具，也不执行
            return self._record_and_return(
                name,
                args,
                spec.level,
                False,
                {
                    "error": (
                        f"权限不足：工具 {name} 属于 {spec.level.label}，"
                        f"本次运行只开放到 {self.max_level.label}"
                    )
                },
                started,
            )
        try:
            result = spec.func(args)
            if not isinstance(result, dict):
                result = {"result": result}
        except Exception as e:  # noqa: BLE001 - 工具异常必须变成观察结果，不中断循环
            result = {
                "error": f"工具执行异常（已捕获）: {type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=3),
            }
        err = _is_error(result)
        return self._record_and_return(name, args, spec.level, not err, result, started, error=err)

    def _record_and_return(
        self,
        name: str,
        args: dict[str, Any],
        level: Permission,
        ok: bool,
        result: dict[str, Any],
        started: float,
        error: str = "",
    ) -> dict[str, Any]:
        duration_ms = int((time.perf_counter() - started) * 1000)
        record = ToolCallRecord(
            name=name,
            args=dict(args),
            level=int(level),
            level_label=level.label,
            ok=ok,
            duration_ms=duration_ms,
            # 兜底：失败却没有原因时，从 result 里把错误捞出来。
            # 起因：两条拒绝路径（未注册工具 / 权限不足）曾漏传 error 参数，
            # 审计于是只记着 ok=False、原因却是空的——而"这次调用为什么被拒"
            # 正是安全关键域里审计要回答的基本问题。放在这里而不是逐个调用点，
            # 是为了让"失败必带原因"成为机制：今后新增拒绝路径不会再漏。
            error=error or (str(result.get("error") or "") if not ok else ""),
            result_chars=len(json.dumps(result, ensure_ascii=False, default=str)),
            result_digest=_digest(result),
        )
        self.audit.append(record)
        return result

    # ---- 上下文安全 ----

    def render_result(self, result: dict[str, Any]) -> str:
        """把工具结果渲染成送给模型的文本（有界截断 + 明确标注截断）。"""
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) <= self.max_result_chars:
            return text
        return text[: self.max_result_chars] + f"\n…[结果已截断，原始长度 {len(text)} 字符]"

    def audit_summary(self) -> dict[str, Any]:
        """审计汇总（自证用：调用次数 / 失败数 / 各级别分布）。"""
        by_level: dict[str, int] = {}
        for r in self.audit:
            by_level[r.level_label] = by_level.get(r.level_label, 0) + 1
        return {
            "calls": len(self.audit),
            "failed": sum(1 for r in self.audit if not r.ok),
            "by_level": by_level,
            "tools_available": self.names(),
        }


__all__ = [
    "MAX_RESULT_CHARS",
    "ToolCallRecord",
    "ToolRegistry",
    "ToolSpec",
]
