"""工具面：注册表（权限/审计）+ 各权限级别的工具实现。

四级已全部落地（共 15 个工具），装配点是 `graph.build_registry`；数字由
`tests/test_tool_surface_docs.py` 对着注册表实测守住：
    R0 只读   9 个  readonly.py 7 个 + sandbox.py 的 dsl_reference / list_drafts
    R1 沙箱写 1 个  sandbox.py draft_test_case（编译期校验 + 可丢弃沙箱）
    R2 真执行 3 个  execution.py 2 个 + sandbox.py run_draft（子进程 + 真超时 + 产物归档）
    R3 持久化 2 个  persist.py write_memory / promote_artifact（人工审批 + 引用门禁）
"""

from .readonly import build_readonly_tools
from .registry import ToolCallRecord, ToolRegistry, ToolSpec

__all__ = [
    "ToolCallRecord",
    "ToolRegistry",
    "ToolSpec",
    "build_readonly_tools",
]
