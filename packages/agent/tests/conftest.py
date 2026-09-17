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
    """离线、短预算的测试配置（轨迹写到 tmp，不污染用户目录）。"""
    return AgentConfig(offline=True, max_steps=6, db_path=tmp_path / "cp.sqlite")
