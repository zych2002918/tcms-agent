"""对外文档的文案门禁：AI 味硬清单（禁用词 + 机械节奏）+ **仪器自证**。

## 为什么进 CI

文案的退化是渐进的：一次加一句「值得注意的是」没人会拦，半年后整篇都是。
而 README 与 ARCHITECTURE 是别人看到这个项目的**第一屏**——最该被机器守着的
恰恰是这层"读起来像不像人写的"。

## 尺子从哪来

`scripts/voice_lint.py` 与本机写作技能里的 `~/.dsh/skills/warm-voice/voice_lint.py`
是**逐字节相同的两份镜像**（本地开发用那份、CI 用这份）。本机会断言哈希一致，
防止"改了技能里的尺子、仓库里的尺子还停在旧版本"。

## 判定阈值

只对 `high`（硬禁用词 / 连续三句同首词）失败。`medium`（「不是…而是…」这类
可能是合法用法）与 `info`（破折号计数）只打印，不阻断——ARCHITECTURE 与 ADR 里
确实存在正常的对举句式，把那类判成错误会让门禁很快被人关掉。

## 扫哪些文件

根 README、`docs/ARCHITECTURE.md`、`docs/decisions.md`、`site/index.html`（项目主页）。
尺子会遮掉代码块、行内代码、表格行、列表项与 HTML 标签——这些都不是句子，
不遮掉的话每一条都会变成误报（这四种误报都真踩过，见 `--selftest` 的反向用例）。

## 零依赖

只 import 标准库 + pytest：CI 的 lint job 只装了 pytest，本文件必须在那里也能跑。
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINTER = ROOT / "scripts" / "voice_lint.py"
SKILL_MIRROR = Path.home() / ".dsh" / "skills" / "warm-voice" / "voice_lint.py"
DOCS = ["README.md", "docs/ARCHITECTURE.md", "docs/decisions.md", "site/index.html"]


def _load_linter() -> Any:
    """按文件路径装载 scripts/voice_lint.py（不依赖 sys.path，不受 import-mode 影响）。"""
    spec = importlib.util.spec_from_file_location("_tcms_voice_lint", LINTER)
    assert spec and spec.loader, f"无法装载 {LINTER}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vl = _load_linter()


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(LINTER), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


# ---------------------------------------------------------------------------
# 1. 执法：对外文档不许踩硬清单
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", DOCS)
def test_outward_docs_have_no_high_hits(doc: str) -> None:
    """README / ARCHITECTURE / ADR 里出现 high 命中即红。

    措辞改了、或者尺子误报（列表项、表格、代码块），都在这条上暴露。
    """
    proc = _run(str(ROOT / doc))
    assert proc.returncode == 0, (
        f"{doc} 文案门禁未过（退出码 {proc.returncode}）：\n{proc.stdout}\n"
        f"命中说明见 stdout 的 high 行；若判定为误报，改 scripts/voice_lint.py 的掩码规则"
    )


# ---------------------------------------------------------------------------
# 2. 仪器自证：尺子必须真的量得出东西
# ---------------------------------------------------------------------------


def test_selftest_passes() -> None:
    """脚本自带的 14 项自证（该抓的 7 项 + 不该抓的 6 项 + 数字守恒）必须全过。"""
    proc = _run("--selftest")
    assert proc.returncode == 0, f"voice_lint --selftest 未通过：\n{proc.stdout}"


def test_gate_detects_a_deliberately_bad_paragraph() -> None:
    """直接喂坏文本：门禁**必须**报 high；喂干净文本必须一条 high 都不报。

    没有这条，正则失配或掩码写宽了都会让门禁长期"全绿"——
    一个恒绿的检查比没有检查更危险（ADR-016 的教训用在门禁自己身上）。
    """
    bad = vl.check_text("本方案赋能全链路，值得注意的是，闭环了。")
    assert any(h.level == "high" and h.rule == "禁用词" for h in bad), "禁用词没被抓到"

    rhythm = vl.check_text("甲走了。甲回来了。甲又走了。")
    assert any(h.level == "high" and h.rule == "节奏" for h in rhythm), "机械节奏没被抓到"

    clean = vl.check_text("引擎 960 条用例跑在真实 DBC 上，覆盖率门禁 97%。")
    assert not [h for h in clean if h.level == "high"], f"干净文本被误报：{[h.text for h in clean]}"


@pytest.mark.skipif(not SKILL_MIRROR.is_file(), reason=f"本机没有技能镜像：{SKILL_MIRROR}")
def test_skill_mirror_is_byte_identical() -> None:
    """本机存在技能副本时，两份尺子必须逐字节相同（只在作者机器上有意义，CI 上跳过）。"""
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    assert digest(LINTER) == digest(SKILL_MIRROR), (
        f"两份 voice_lint.py 已经分叉：\n  {LINTER}\n  {SKILL_MIRROR}\n"
        "改哪份都要把另一份覆盖过去（Copy-Item 即可）"
    )
