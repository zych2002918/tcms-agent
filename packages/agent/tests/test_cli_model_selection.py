"""换模型：CLI 与评测臂都要能指名道姓地选一个模型。

## 为什么单独守这条

用户的原话是："我需要你支持手动获取模型然后选择模型……如果是其他轻量级模型
或者不同模型呢？是否仍有目前效果？"

"效果还在不在"不该靠断言，得能**测**。所以模型必须是一等公民：
- `run --model X` 能指定本次运行的模型（并如实报出实际用的是哪个）；
- `eval run --model X` 能把某个模型做成对照臂（`llm@X`），
  与其它模型在同一任务集上横向比。

这里守的是"接线正确"：臂的名字/覆盖项/依赖声明，
以及解析器确实收下了这几个参数（免得参数写了却没接到 config 上）。
"""

from __future__ import annotations

from tcms_agent.cli import build_parser
from tcms_agent.eval.harness import model_arms


def test_model_arms_is_one_named_arm():
    """`--model X` → 恰好一个臂，名字自带模型名（报告里也就自带口径）。"""
    arms = model_arms("qwen-turbo")
    assert len(arms) == 1
    a = arms[0]
    assert a.name == "llm@qwen-turbo"
    assert a.requires_llm is True, "模型臂必须声明需要 key，否则会在无 key 时静默跑出假结果"
    assert a.overrides == {"offline": False, "model": "qwen-turbo"}


def test_model_arms_carries_base_url_when_given():
    arms = model_arms("m", "https://example.invalid/v1")
    assert arms[0].overrides["base_url"] == "https://example.invalid/v1"


def test_run_parser_accepts_model_and_base_url():
    args = build_parser().parse_args(
        ["run", "验证车门故障", "--llm", "--model", "qwen-turbo", "--base-url", "https://e/v1"]
    )
    assert args.model == "qwen-turbo"
    assert args.base_url == "https://e/v1"


def test_eval_run_parser_accepts_model():
    args = build_parser().parse_args(["eval", "run", "--model", "deepseek-v4.1-flash"])
    assert args.model == "deepseek-v4.1-flash"
    assert args.base_url is None


def test_cmd_run_passes_model_into_config(monkeypatch, tmp_path):
    """参数必须真的进 AgentConfig —— 只加 flag 不接线是最容易漏的一步。"""
    from tcms_agent import cli

    seen: dict = {}

    class FakeRunner:
        def __init__(self, cfg):  # noqa: ANN001
            seen["cfg"] = cfg

        def run(self, goal, thread_id=None, approver=None):  # noqa: ANN001, ARG002
            raise RuntimeError("到此为止：只验证 config 接线")

    monkeypatch.setattr(cli, "AgentRunner", FakeRunner)
    monkeypatch.setattr(cli, "llm_available", lambda: True)
    args = build_parser().parse_args(
        [
            "run",
            "验证车门故障",
            "--llm",
            "--model",
            "tiny",
            "--base-url",
            "https://e/v1",
            "--db",
            str(tmp_path / "t.db"),
            "--memory",
            str(tmp_path / "mem"),
            "--sandbox",
            str(tmp_path / "sb"),
        ]
    )
    try:
        cli.cmd_run(args)
    except RuntimeError:
        pass
    cfg = seen["cfg"]
    assert cfg.model == "tiny"
    assert cfg.base_url == "https://e/v1"
    assert cfg.offline is False
