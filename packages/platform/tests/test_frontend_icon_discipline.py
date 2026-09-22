"""前端图标纪律的**静态门禁**：图标槽位不许再用 Unicode 字形。

## 为什么需要它

2026-09-22 定的规则（写在 `components/icons.tsx` 模块头）：
**自己独占一个"图形位" → 用 SVG；旁边有词 → 它是排版标点。**

但规则写在文档里 ≠ 会被执行。那一轮把全站字形换成内联 SVG 时扫了**两遍**
（一遍人工、一遍子代理），仍然漏掉一整批：`⬅`（4 处返回按钮）、`🔍`（图谱搜索框前导图标）、
`🔎`（症状诊断按钮）、`🎉`（引导完成页）、`⌖ ⌕ ↺ ⛔ ◇ ⧉ ⟲ ⇄ ↩`、`🔒 🚄`。
**能被机器判定的规则，就不要靠人记得执行。**

## 判据（两条，都不需要"看懂意图"）

1. 源码里出现**图标类字形**（几何图形 / 装饰符号 / 方向与勾叉类 / emoji / 变体选择符 U+FE0F）→ 失败。
   正文标点 `→ ← · ✓ ✗ △ ▲ ⚠ ✔ ⚡` **允许**：它们旁边有词，是排版符号而不是图标
   （硬换成 SVG 只会让行内文字基线错位）。
2. `components/icons.tsx` 的每个导出图标都必须走**共享规格**（16 视框 / 1.5 线宽 / currentColor），
   否则"统一规格"会随新增图标悄悄失效 —— 那正是当初字符图标"看起来拼凑"的原因。

注释先剥掉再扫：注释里提到字形（模块文档、`原先是 ⌖`、`不用 ⛔`）是合理的。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"
ICON_FILE = WEB_SRC / "components" / "icons.tsx"

# 图标类字形：出现在 UI 里就说明"又用手写字符当图标了"
FORBIDDEN_GLYPHS = set("▶◀◫◍◈✦◎◌⌖⌕↺↻⟲⇄↩⧉⬅◇◆★☆✕✖⛔⤢⏸⏵▤▥▦●○◐◑■□☝✚✱➤➔")

# 明确允许的正文标点与键盘符号（旁边有词 → 排版符号，不是图标）
#   `⌘` 是 Mac 命令键的标准写法（"Ctrl/⌘+Enter"），跟 `→` 一样属于文案而非图标。
ALLOWED_MARKS = set("→←·✓✗△▲⚠✔⚡⌘⌥⇧⌃")

_JSX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.S)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")

_ICON_SIGNATURE = re.compile(
    r"export function (Icon\w+)\(p: IconProps\) \{\s*return \(\s*<svg \{\.\.\.BASE\} \{\.\.\.p\}>",
    re.S,
)


def _strip_comments(text: str) -> str:
    """把注释换成等量空行（保留行号，便于报错定位）。"""
    for pattern in (_JSX_COMMENT, _BLOCK_COMMENT, _LINE_COMMENT):
        text = pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def _sources() -> list[Path]:
    if not WEB_SRC.is_dir():
        pytest.skip(f"前端源码不存在: {WEB_SRC}")
    return sorted(p for p in WEB_SRC.rglob("*") if p.suffix in {".ts", ".tsx"})


def _is_forbidden(ch: str) -> bool:
    if ch in ALLOWED_MARKS:
        return False
    return ch in FORBIDDEN_GLYPHS or ord(ch) >= 0x1F000 or ch == "\ufe0f"


def test_no_glyph_icons_in_frontend_sources() -> None:
    """图标槽位不许再出现 Unicode 字形（注释与正文标点不受影响）。"""
    offenders: list[str] = []
    for path in _sources():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(text.splitlines(), 1):
            hit = next((c for c in line if _is_forbidden(c)), None)
            if hit is not None:
                rel = path.relative_to(WEB_SRC.parents[1])
                offenders.append(f"  {rel}:{lineno}  {hit!r}  {line.strip()[:78]}")
    assert not offenders, (
        f"图标槽位又出现 Unicode 字形（{len(offenders)} 处）。"
        "请改用 `components/icons.tsx` 里的 SVG 组件（缺哪个就补一个，规格见模块头）。\n"
        + "\n".join(offenders)
    )


def test_every_icon_uses_the_shared_spec() -> None:
    """新增图标必须走共享规格，否则"统一规格"会悄悄失效。"""
    if not ICON_FILE.is_file():
        pytest.skip(f"图标模块不存在: {ICON_FILE}")
    text = ICON_FILE.read_text(encoding="utf-8")
    exported = re.findall(r"export function (Icon\w+)", text)
    assert exported, "icons.tsx 里没有导出任何图标"
    on_spec = set(_ICON_SIGNATURE.findall(text))
    missing = [name for name in exported if name not in on_spec]
    assert not missing, (
        f"这些图标没走共享规格（`<svg {{...BASE}} {{...p}}>`）：{missing}。"
        "自定 viewBox / 线宽会让图标在同一行里粗细不一。"
    )


def test_base_spec_is_the_agreed_one() -> None:
    """共享规格本身被改动时要显式确认：16 视框 / 1.5 线宽 / currentColor / aria-hidden。"""
    if not ICON_FILE.is_file():
        pytest.skip(f"图标模块不存在: {ICON_FILE}")
    text = ICON_FILE.read_text(encoding="utf-8")
    assert 'viewBox: "0 0 16 16"' in text, "共享视框被改了（全站尺寸会一起漂）"
    assert "strokeWidth: 1.5" in text, "共享线宽被改了（粗细则不再统一）"
    assert 'stroke: "currentColor"' in text, "图标必须跟随文字颜色（否则深/浅色主题会失配）"
    assert '"aria-hidden": "true"' in text, "图标必须对读屏隐藏（文字已经说明了含义）"
