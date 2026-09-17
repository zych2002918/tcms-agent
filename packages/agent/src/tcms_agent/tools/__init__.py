"""工具面：注册表（权限/审计）+ 各权限级别的工具实现。

当前进度：
    R0 只读   ✅ readonly.py（7 个工具）
    R1 沙箱写 ⬜ 计划于 M3
    R2 真执行 ⬜ 计划于 M3（超时 + 产物归档）
    R3 持久化 ⬜ 计划于 M3（人工审批）
"""

from .readonly import build_readonly_tools
from .registry import ToolCallRecord, ToolRegistry, ToolSpec

__all__ = [
    "ToolCallRecord",
    "ToolRegistry",
    "ToolSpec",
    "build_readonly_tools",
]
