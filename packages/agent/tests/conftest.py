"""pytest 共享夹具。

知识底座构建一次（约 1s，加载真实资产 + 建图 + 域注入），整个会话复用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tcms_agent.config import AgentConfig
from tcms_agent.knowledge import build_knowledge


@pytest.fixture(scope="session")
def knowledge():
    """真实知识底座（资产模型 + 图谱 + 混合检索）。"""
    return build_knowledge()


@pytest.fixture
def cfg(tmp_path: Path) -> AgentConfig:
    """离线、短预算的测试配置。

    **所有落盘位置都必须指到 tmp**：db（轨迹）/ memory（记忆+运行日志）/ sandbox（草稿+
    执行产物）/ artifacts（正式归档）。少隔离任何一个，测试都会污染用户的真实
    `~/.tcms-agent/` 目录——这类"测试写脏用户环境"的缺陷很容易在本地被忽略。
    """
    return AgentConfig(
        offline=True,
        max_steps=10,
        db_path=tmp_path / "cp.sqlite",
        memory_dir=tmp_path / "memory",
        sandbox_dir=tmp_path / "sandbox",
        artifacts_dir=tmp_path / "artifacts",
    )
