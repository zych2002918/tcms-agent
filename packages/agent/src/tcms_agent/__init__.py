"""TCMS 全流程测试工程师 Agent。

分层：agent → platform（知识底座/资产模型）→ engine（领域引擎与真实资产）。

模块地图：
    state       AgentState 与预算（步数/成本）定义
    config      运行期配置（模型、预算、上游路径、数据库位置）
    models      LLM 工厂：真模型（OpenAI 兼容）/ 离线脚本模型（确定性，可复现）
    tools/      工具注册表（四级权限）+ 只读领域工具
    nodes      图节点：plan / agent / verify / report
    graph       LangGraph StateGraph 组装
    runner      运行器：thread_id、checkpointer、结果收集
    cli         命令行入口
"""

from ._version import __version__

__all__ = ["__version__"]
