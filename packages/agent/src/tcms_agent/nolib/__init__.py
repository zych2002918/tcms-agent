"""nolib —— 手写最小 Agent loop（对照仪器，不是第二个实现）。

用途：把"LangGraph 到底提供了什么"从一句感觉变成可测量的对照。

    from tcms_agent.nolib import run_goal
    res = run_goal("验证车门故障必须触发降级处置")   # 返回与框架版同一个 AgentRunResult

设计与边界见 `loop.py` 的模块文档：**复用同一批节点与工具，只替换编排层**，
因此两版结果应当一致——一致性本身就是结论（框架的价值不在"让结果更好"）。
"""

from .compare import capability_matrix, code_size, parity_report, render, verify_capabilities
from .loop import run_goal

__all__ = [
    "capability_matrix",
    "code_size",
    "parity_report",
    "render",
    "run_goal",
    "verify_capabilities",
]
