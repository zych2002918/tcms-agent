"""设计 token 的对比度门禁：颜色不是"看着舒服"就行，得能读。

**为什么需要它**：本仓大量使用 10.5–12px 的小字（提示、单位、图例、表头），而三级文字
`--ink-faint` 恰恰是最容易被"调浅一点更好看"改坏的一个。真被改坏过一次：浅色主题下
`--ink-faint` 对白底只有 3.09:1、`--ok` 3.39:1、`--warn` 3.62:1，三条都低于 WCAG AA
对正文要求的 4.5:1。这类问题在深色主题上几乎看不出来（同一组在深色下是 4.0 / 9.6 / 10.6），
所以"改的人自己扫一眼"守不住，只能让机器守。

**口径**（WCAG 2.1 AA）：

- 文字（含 10.5px 小字）→ **4.5:1**
- 非文字（图形、状态点、控件边界）→ **3.0:1**
- 面板之间的**分隔细线**（`--line`）是装饰性分区，不属于"识别控件/状态所必需的信息"
  （WCAG 1.4.11 管的是后者），不适用 3:1；本仓按 **1.2:1** 设下限 —— 硬拉到 3:1 会让
  界面变成"框套框"，那是另一种失败。

判据取自 `web/src/index.css` **实际解析出来的值**，不在这里抄一份常量：
抄一份就等于给自己留了个会说谎的副本。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[1] / "web" / "src" / "index.css"

# (前景, 背景, 门槛, 用途) —— 门槛见模块 docstring
CHECKS: list[tuple[str, str, float, str]] = [
    ("--ink", "--bg", 4.5, "页面主文字落底色"),
    ("--ink", "--surface", 4.5, "面板主文字"),
    ("--ink", "--surface-2", 4.5, "表头/分段控件上的主文字"),
    ("--ink-dim", "--surface", 4.5, "次级说明文字"),
    ("--ink-dim", "--surface-2", 4.5, "次级说明文字（抬升面）"),
    ("--ink-faint", "--surface", 4.5, "三级文字（提示/单位），全站最小字号"),
    ("--ink-faint", "--surface-2", 4.5, "三级文字（抬升面）"),
    ("--ink-faint", "--bg", 4.5, "底色上的三级文字"),
    ("--ok", "--surface", 4.5, "PASS/通过 Tag 文字"),
    ("--warn", "--surface", 4.5, "降级/警告 Tag 文字"),
    ("--bad", "--surface", 4.5, "FAIL/故障 Tag 文字"),
    ("--info", "--surface", 4.5, "信息/操作 Tag 文字"),
    ("--vio", "--surface", 4.5, "信号/枚举 Tag 文字"),
    ("--line", "--surface", 1.2, "面板分隔细线（装饰性，非控件边界）"),
]

# 主题选择器 → 人读名字。深色块写在 `:root, html.theme-dark` 里。
THEME_SELECTORS: list[tuple[str, str]] = [
    (r":root\s*,\s*html\.theme-dark\s*\{", "dark"),
    (r"html\.theme-light\s*\{", "light"),
]

_HEX = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;")



def _block(text: str, selector: str) -> str:
    """取出某个主题块的内容（这些块是平的，不含嵌套花括号）。"""
    m = re.search(selector, text)
    if m is None:
        raise AssertionError(f"index.css 里找不到主题选择器：{selector}")
    end = text.find("}", m.end())
    assert end != -1, f"主题块没有闭合：{selector}"
    return text[m.end() : end]


def _tokens(text: str, selector: str) -> dict[str, str]:
    """只收 `#rrggbb` 形式；`rgba(...)` 这类透明度色不参与对比度计算。"""
    return {f"--{name}": value for name, value in _HEX.findall(_block(text, selector))}


@pytest.fixture(scope="module")
def themes() -> dict[str, dict[str, str]]:
    if not CSS.is_file():
        pytest.skip(f"前端样式不存在: {CSS}")
    text = CSS.read_text(encoding="utf-8")
    return {name: _tokens(text, sel) for sel, name in THEME_SELECTORS}


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_both_themes_define_every_checked_token(themes, theme):
    """两种主题都得定义被检查的 token —— 少一个就是"亮色下这块没颜色"。"""
    toks = themes[theme]
    for fg, bg, _need, _what in CHECKS:
        assert fg in toks, f"{theme} 主题缺 {fg}"
        assert bg in toks, f"{theme} 主题缺 {bg}"


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(
    ("fg", "bg", "need", "what"),
    CHECKS,
    ids=[f"{c[0]}-on-{c[1]}" for c in CHECKS],
)
def test_contrast_meets_wcag_aa(themes, theme, fg, bg, need, what):
    toks = themes[theme]
    ratio = contrast(toks[fg], toks[bg])
    assert ratio >= need, (
        f"{theme} 主题：{fg}({toks[fg]}) 落在 {bg}({toks[bg]}) 上只有 {ratio:.2f}:1，"
        f"低于要求的 {need}:1 —— {what}。"
        f"\n改法：调深/调浅 index.css 里对应主题的 {fg}（不要只改一个主题就完事）。"
    )
