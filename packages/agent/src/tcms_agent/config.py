"""Agent 运行期配置（预算、模型、存储位置）。

设计纪律（继承原三仓）：
- **离线优先**：无 API key 时必须仍能跑完整条链路（用确定性脚本模型），
  绝不因为缺 key 就崩；是否真用了 LLM 由 `used_llm` 如实标注。
- **预算受控**：步数 / 递归深度 / 温度全部显式配置，不靠默认值兜底。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .permissions import Permission


def default_db_path() -> Path:
    """checkpoint 数据库默认位置（可用环境变量覆盖）。

    轨迹持久化是本项目的一等公民（情景记忆的底座），因此默认落到用户目录下
    的稳定位置，而不是仓库内临时文件。
    """
    env = os.environ.get("TCMS_AGENT_DB")
    if env:
        return Path(env)
    return Path.home() / ".tcms-agent" / "checkpoints.sqlite"


@dataclass(frozen=True)
class AgentConfig:
    """一次 Agent 运行的完整配置。"""

    # --- 预算 ---
    max_steps: int = 12
    """ReAct 循环的最大决策步数（超出即强制收尾，防死循环）。"""

    recursion_limit: int = 40
    """LangGraph 超级步上限（兜底保护；与 max_steps 独立计量）。"""

    # --- 模型 ---
    offline: bool = False
    """强制离线脚本模型（忽略 API key）。用于测试与可复现实验。"""

    temperature: float = 0.0
    """默认 0.0：Agent 决策要可复现，创造性留给生成类工具。"""

    model: str | None = None
    """覆盖模型名；None 表示走 platform 的解析链（env → 本地设置 → 默认）。"""

    base_url: str | None = None
    """覆盖 base_url；None 表示走 platform 的解析链。"""

    # --- 工具权限 ---
    max_level: Permission = Permission.EXECUTE
    """本次运行允许暴露给模型的**最高**工具级别（注册表按 `level <= max_level` 裁剪）。

    默认到 R2 真执行，理由：
      - 场景执行对被测系统是**只读**的（跑仿真、读断言，不改任何东西），
        而"能真跑"正是测试工程师 Agent 存在的意义；
      - 更高的 R3 持久化（写记忆 / 提交用例）默认**关闭**，必须显式开启并过人工审批。
    需要完全无副作用时（如纯问答、CI 的保守档）设为 `Permission.READ`。
    """

    # --- 执行 ---
    exec_timeout_s: float = 60.0
    """单次场景执行的超时（秒）。超时在独立子进程上强制执行，不是"放弃等待"。"""

    sandbox_dir: Path = field(default_factory=lambda: Path.home() / ".tcms-agent" / "sandbox")
    """执行产物归档根目录（每次运行一个 run_id 子目录）。"""

    # --- 存储 ---
    db_path: Path = field(default_factory=default_db_path)
    """轨迹（checkpoint）落盘的 SQLite 路径。"""

    # --- 知识底座 ---
    upstream: Path | None = None
    """上游引擎目录；None 表示用 platform 的 resolve_asset_source() 自动解析。"""

    def with_(self, **kw) -> AgentConfig:
        """返回替换若干字段后的新配置（frozen dataclass 的便捷写法）。"""
        from dataclasses import replace

        return replace(self, **kw)
