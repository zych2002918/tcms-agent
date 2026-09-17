"""分发卫生：让"改一行脚本就把中文搞乱"这类问题在回归时就暴露。

## 为什么需要这个文件

这些约束**只有在别人拿到包时才会出事**，本机怎么跑都是好的：

1. **`.ps1` 必须带 UTF-8 BOM。** PowerShell 5.1 读无 BOM 的脚本时按 ANSI(GBK) 解析，
   里面的中文会全部乱码。而这个 BOM **极易被编辑器悄悄吃掉**——本仓就发生过：
   用编辑工具改了两行 `agent-demo.ps1`，BOM 就没了，直到打包自检才报出来
   （"缺少 UTF-8 BOM（中文会乱码）"）。所以必须在回归里守住，而不是等到打包。

2. **`.bat` 必须保持纯 ASCII。** cmd.exe 按 OEM 代码页解析批处理文件，
   非 ASCII 内容会乱码，且 `chcp 65001` 并不能可靠挽救。
   因此约定：`.bat` 只做壳（调用 `.ps1`），中文一律放 `.ps1`。

3. **Windows 脚本必须 CRLF。** 行尾由 `.gitattributes` 强制，这里做兜底断言。

4. **`使用说明.txt` 里的入口必须真实存在。** 它对新用户是唯一指引，
   提到一个不存在的文件比不写更糟。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", "_offline", "dist", ".git", "__pycache__"}

BOM = b"\xef\xbb\xbf"


def _walk(pattern: str) -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob(pattern):
        if SKIP_DIRS & set(p.parts):
            continue
        out.append(p)
    return sorted(out)


def test_all_ps1_have_utf8_bom() -> None:
    """`.ps1` 少了 BOM，用户那边的中文提示会变成乱码。

    这是最容易复发的约束：任何"另存 / 重写文件"的操作都可能把 BOM 吃掉。
    """
    files = _walk("*.ps1")
    assert files, "应能找到启动脚本"
    missing = [str(p.relative_to(ROOT)) for p in files if not p.read_bytes().startswith(BOM)]
    assert missing == [], (
        f"以下 .ps1 缺少 UTF-8 BOM（PowerShell 5.1 会把中文读成乱码）：{missing}\n"
        "修复：python -c \"import pathlib;[p.write_bytes(b'\\xef\\xbb\\xbf'+p.read_bytes()) "
        "for p in pathlib.Path('.').rglob('*.ps1') if not p.read_bytes().startswith(b'\\xef\\xbb\\xbf')]\""
    )


def test_all_bat_are_pure_ascii() -> None:
    """`.bat` 不能出现非 ASCII —— cmd 会按 OEM 代码页解析成乱码。"""
    files = _walk("*.bat")
    assert files, "应能找到启动壳脚本"
    bad: list[str] = []
    for p in files:
        raw = p.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as e:
            bad.append(f"{p.relative_to(ROOT)}（首个非 ASCII 字节在偏移 {e.start}）")
    assert bad == [], f"以下 .bat 含非 ASCII 字符，cmd 下会乱码：{bad}"


def test_windows_scripts_use_crlf() -> None:
    """Windows 脚本用 CRLF（cmd 对 LF-only 批处理并不可靠）。"""
    bad: list[str] = []
    for p in _walk("*.bat") + _walk("*.ps1"):
        raw = p.read_bytes()
        if b"\r\n" not in raw or raw.count(b"\n") != raw.count(b"\r\n"):
            bad.append(str(p.relative_to(ROOT)))
    assert bad == [], f"以下脚本行尾不是纯 CRLF：{bad}"


def test_shell_scripts_use_lf() -> None:
    """shell 脚本用 LF（CRLF 会让 bash 报 `\\r: command not found`）。"""
    bad = [str(p.relative_to(ROOT)) for p in _walk("*.sh") if b"\r\n" in p.read_bytes()]
    assert bad == [], f"以下 shell 脚本含 CRLF：{bad}"


def test_every_launcher_mentioned_in_readme_exists() -> None:
    """说明文档里提到的启动入口必须真实存在——对新用户那是唯一指引。"""
    doc = ROOT / "使用说明.txt"
    assert doc.is_file(), "缺少《使用说明.txt》（新用户的唯一指引）"
    text = doc.read_text(encoding="utf-8")
    for entry in ("start.bat", "agent-demo.bat", "test.bat", "_offline"):
        assert entry in text, f"《使用说明.txt》没有提到 {entry}"
        assert (ROOT / entry).exists(), f"《使用说明.txt》提到的 {entry} 实际不存在"


def test_release_builder_present_and_importable() -> None:
    """打包脚本必须存在且语法正确（否则"出包"这一步会临时才发现坏）。"""
    import ast

    p = ROOT / "scripts" / "build_release.py"
    assert p.is_file(), "缺少 scripts/build_release.py"
    ast.parse(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["README.md", "使用说明.txt", "docs/ARCHITECTURE.md", "docs/decisions.md"])
def test_key_docs_are_utf8_without_bom(name: str) -> None:
    """面向用户的文档应为无 BOM 的 UTF-8（带 BOM 会让部分工具显示多余字符）。"""
    p = ROOT / name
    assert p.is_file(), f"缺少 {name}"
    raw = p.read_bytes()
    assert not raw.startswith(BOM), f"{name} 带了 BOM，多数 Markdown 工具会显示成多余字符"
    raw.decode("utf-8")  # 必须是合法 UTF-8
