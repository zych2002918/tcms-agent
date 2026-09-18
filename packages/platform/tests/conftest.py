"""platform 测试的**默认隔离**：不要让单元测试花用户的钱、也不要让结论随本机而变。

为什么需要它（这是真实风险，不是洁癖）：

key 解析链是 `env → 本机设置(~/.tcms-ai-platform) → DSH 凭据文件(~/.dsh)`。
于是本机一旦配过 key（开发机基本都配过），`_make_harness()` 就会走**真 LLM**：
测试会真的发请求、真的计费，而且"同一个测试在两台机器上行为不同"——
测试的可信度直接归零。同类坑本仓已经踩过一次（测试写入用户真实
`~/.tcms-agent` 目录），处置方式相同：**默认隔离，要真的才显式打开**。

因此这里把两条外部来源都指向临时路径：
- `DSH_CREDENTIALS_FILE` → 不存在的临时文件（凭据文件来源失效）
- `TCMS_AI_HOME` → 临时目录（本机设置来源失效）
- 四个 key 环境变量一并清掉

需要 LLM 行为的测试不必打开真网络：显式
`monkeypatch.setenv("DASH_API_KEY", "sk-test")` 让"有 key"成立，再打桩对话函数即可。
真要连真实端点时才设 `TCMS_ALLOW_REAL_LLM=1`（会让测试不确定，本仓不推荐）。
"""

from __future__ import annotations

import os

import pytest

_KEY_ENVS = ("DASH_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY")


@pytest.fixture(autouse=True)
def _isolate_llm_credentials(tmp_path, monkeypatch):
    """默认让"本机配过 key"这件事对测试**不可见**。"""
    if os.environ.get("TCMS_ALLOW_REAL_LLM") == "1":
        return
    for k in _KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DSH_CREDENTIALS_FILE", str(tmp_path / "no-such-credentials.yaml"))
    monkeypatch.setenv("TCMS_AI_HOME", str(tmp_path / "tcms-home"))
