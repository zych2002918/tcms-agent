"""工具注册表：权限门禁 / 诚实错误 / 审计 / 上下文安全。"""

from __future__ import annotations

from tcms_agent.permissions import Permission
from tcms_agent.tools.registry import ToolRegistry, ToolSpec


def _spec(name: str, level: Permission, fn, params: dict | None = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"测试工具 {name}",
        level=level,
        parameters=params or {"type": "object", "properties": {}},
        func=fn,
    )


# ---------------------------------------------------------------------------
# 权限门禁
# ---------------------------------------------------------------------------


def test_schema_exposes_only_allowed_levels() -> None:
    reg = ToolRegistry(max_level=Permission.READ)
    reg.register(_spec("r0", Permission.READ, lambda a: {"ok": 1}))
    reg.register(_spec("r2", Permission.EXECUTE, lambda a: {"ok": 2}))
    reg.register(_spec("r3", Permission.PERSIST, lambda a: {"ok": 3}))
    names = [s["function"]["name"] for s in reg.schemas()]
    assert names == ["r0"], "高于允许级别的工具不得出现在送给模型的 schema 里"
    assert reg.names(allowed_only=False) == ["r0", "r2", "r3"]


def test_invoke_refuses_above_level_and_does_not_execute() -> None:
    """双保险：即使模型绕过 schema 直接点名高权限工具，也不得执行。"""
    called = {"n": 0}

    def danger(_args):
        called["n"] += 1
        return {"ok": True}

    reg = ToolRegistry(max_level=Permission.READ)
    reg.register(_spec("rm_rf", Permission.PERSIST, danger))
    result = reg.invoke("rm_rf", {})
    assert "权限不足" in result["error"]
    assert called["n"] == 0, "被拒的调用绝不能真的执行"
    assert reg.audit[-1].ok is False
    assert reg.audit[-1].level_label == "R3 持久化"


def test_persist_level_requires_approval_flag() -> None:
    assert Permission.PERSIST.requires_approval is True
    assert Permission.EXECUTE.requires_approval is False
    assert Permission.READ.requires_approval is False


# ---------------------------------------------------------------------------
# 诚实错误
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_honest_error() -> None:
    reg = ToolRegistry()
    result = reg.invoke("not_registered", {})
    assert "未注册的工具" in result["error"]
    assert len(reg.audit) == 1


def test_tool_exception_is_contained_not_raised() -> None:
    def boom(_args):
        raise RuntimeError("内部炸了")

    reg = ToolRegistry()
    reg.register(_spec("boom", Permission.READ, boom))
    result = reg.invoke("boom", {})
    assert "工具执行异常（已捕获）" in result["error"]
    assert "RuntimeError" in result["error"]
    assert reg.audit[-1].ok is False


def test_non_dict_args_is_tolerated() -> None:
    reg = ToolRegistry()
    reg.register(_spec("echo", Permission.READ, lambda a: {"got": a}))
    assert reg.invoke("echo", None) == {"got": {}}


def test_tool_error_key_marks_record_failed() -> None:
    """工具用 {"error": ...} 表达失败时，审计必须记为失败。"""
    reg = ToolRegistry()
    reg.register(_spec("soft", Permission.READ, lambda a: {"error": "业务层失败"}))
    reg.invoke("soft", {})
    assert reg.audit[-1].ok is False
    assert reg.audit[-1].error == "业务层失败"


# ---------------------------------------------------------------------------
# 审计与上下文安全
# ---------------------------------------------------------------------------


def test_duplicate_registration_rejected() -> None:
    reg = ToolRegistry()
    reg.register(_spec("dup", Permission.READ, lambda a: {}))
    try:
        reg.register(_spec("dup", Permission.READ, lambda a: {}))
    except ValueError as e:
        assert "重名" in str(e)
    else:  # pragma: no cover
        raise AssertionError("重名注册必须报错（防静默覆盖）")


def test_audit_records_duration_and_digest() -> None:
    reg = ToolRegistry()
    reg.register(_spec("t", Permission.READ, lambda a: {"v": 42}))
    reg.invoke("t", {"x": 1})
    rec = reg.audit[-1]
    assert rec.name == "t"
    assert rec.args == {"x": 1}
    assert rec.duration_ms >= 0
    assert len(rec.result_digest) == 12
    summary = reg.audit_summary()
    assert summary["calls"] == 1 and summary["failed"] == 0


def test_render_result_truncates_with_explicit_marker() -> None:
    reg = ToolRegistry()
    reg.max_result_chars = 50
    text = reg.render_result({"blob": "x" * 500})
    assert "结果已截断" in text
    assert len(text) < 200


def test_render_result_keeps_small_payload_intact() -> None:
    reg = ToolRegistry()
    text = reg.render_result({"a": 1})
    assert "截断" not in text
    assert '"a": 1' in text
