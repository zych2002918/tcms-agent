"""Prompt 构造与 CLI 测试。"""

from tcms_ai_testgen.cli import main as cli_main
from tcms_ai_testgen.models import GenRequest
from tcms_ai_testgen.prompt import build_system_prompt, build_user_prompt


class TestPrompt:
    def test_system_prompt_contract(self) -> None:
        sp = build_system_prompt()
        assert "cases" in sp
        assert "expected" in sp
        assert "covers" in sp
        assert "json" in sp.lower() or "JSON" in sp

    def test_user_prompt_injects_req(self) -> None:
        req = GenRequest(
            target="EBM",
            requirements=["超速触发制动", "断线告警"],
            hints=["边界0/限速值"],
            num_cases=7,
            tier="safety",
        )
        up = build_user_prompt(req)
        assert "EBM" in up
        assert "超速触发制动" in up
        assert "边界0/限速值" in up
        assert "7" in up
        assert "safety" in up

    def test_user_prompt_empty_req_ok(self) -> None:
        req = GenRequest(target="X")
        up = build_user_prompt(req)
        assert "需求清单" not in up  # 无需求时不输出该节


class TestCli:
    def test_cli_json_mode(self, capsys) -> None:
        code = cli_main_with(["--num", "4", "--json"])
        assert code == 0
        captured = capsys.readouterr().out
        assert '"parsed"' in captured

    def test_cli_human_mode(self, capsys) -> None:
        code = cli_main_with(["--num", "3"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "parse_rate" in captured


def cli_main_with(argv: list[str]) -> int:
    import sys

    old = sys.argv
    sys.argv = ["cli"] + argv
    try:
        return cli_main()
    finally:
        sys.argv = old
