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

4. **`0-先读我-使用说明.txt` 里的入口必须真实存在。** 它对新用户是唯一指引，
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
    """说明文档里提到的启动入口必须真实存在——对新用户那是唯一指引。

    **注意 `_offline/` 要特殊对待**：它是**发布物**而不是仓库内容
    （内含 49 MB 的 uv.exe，刻意不入 git，由打包脚本放进去）。
    本测试第一版直接断言 `(ROOT / "_offline").exists()`，
    本地因为它被上一次打包创建过而通过，**在 CI 的干净检出上必红**——
    典型的"因为错误的原因通过"。现在改为断言"打包脚本确实会放入它"，
    那才是这句话真正该守的东西。
    """
    doc = NOVICE_DOC
    assert doc.is_file(), f"缺少《{NOVICE_DOC.name}》（新用户的唯一指引）"
    text = doc.read_text(encoding="utf-8")

    # ① 仓库里就有的入口：必须真的存在
    for entry in ("start.bat", "agent-demo.bat", "test.bat", "tcms.bat"):
        assert entry in text, f"《{NOVICE_DOC.name}》没有提到 {entry}"
        assert (ROOT / entry).exists(), f"《{NOVICE_DOC.name}》提到的 {entry} 实际不存在"

    # ② 发布物：文档要提到，且打包脚本要负责放进去（不在 git 里是正常的）
    assert "_offline" in text, f"《{NOVICE_DOC.name}》没有提到 _offline"
    builder = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert "_offline" in builder, (
        "打包脚本没有把 _offline 放进包，但新手文档告诉用户去那里找 uv.exe"
    )
    assert (ROOT / "_offline").exists() or "uv.exe" in builder, (
        "打包脚本里看不出会放入 uv.exe"
    )


def test_novice_doc_does_not_tell_users_to_run_bare_uv() -> None:
    """新手文档不能只教 `uv run ...` —— 新电脑通常没有全局 uv。

    这条是被实测逼出来的：在一台"没装 uv（用包内自带的）"的机器上，
    说明书里的 `uv run tcms-agent tools` 会失败成
        'uv' is not recognized as an internal or external command
    即便装了 uv，在别的目录执行也会失败成
        error: Failed to spawn: `tcms-agent`
    因此统一改用 `tcms.bat` 包装器（自动找 uv + 自动切目录）。
    如果文档里出现裸 `uv run tcms-agent`，说明有人把这条改回去了。
    """
    text = NOVICE_DOC.read_text(encoding="utf-8")
    offenders = [
        (i, ln.strip())
        for i, ln in enumerate(text.splitlines(), 1)
        # 允许作为"等价说法"出现在解释句里（含"等价于"的那行）
        if "uv run tcms-agent" in ln and "等价" not in ln
    ]
    assert offenders == [], (
        f"新手文档里出现了裸 uv 命令（新电脑上大概率不存在 uv）：{offenders[:3]}"
    )


def test_release_builder_present_and_importable() -> None:
    """打包脚本必须存在且语法正确（否则"出包"这一步会临时才发现坏）。"""
    import ast

    p = ROOT / "scripts" / "build_release.py"
    assert p.is_file(), "缺少 scripts/build_release.py"
    ast.parse(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["README.md", "0-先读我-使用说明.txt", "docs/ARCHITECTURE.md", "docs/decisions.md"])
def test_key_docs_are_utf8_without_bom(name: str) -> None:
    """面向用户的文档应为无 BOM 的 UTF-8（带 BOM 会让部分工具显示多余字符）。"""
    p = ROOT / name
    assert p.is_file(), f"缺少 {name}"
    raw = p.read_bytes()
    assert not raw.startswith(BOM), f"{name} 带了 BOM，多数 Markdown 工具会显示成多余字符"
    raw.decode("utf-8")  # 必须是合法 UTF-8


# ---------------------------------------------------------------------------
# 文档里对用户做出的**可判定声明**必须与实物一致
# ---------------------------------------------------------------------------
#
# 起因：一次人工巡检发现说明书里有 4 处 markdown 星号（纯文本里原样显示）、
# ADR 条数写着 18 而实际已经 22、页面清单与实际导航对不上。
# 这些都是"对用户说的话与实物不符"，本可自动化，因此固化在这里。
#
# 原则：**能被机器判定的声明，就不要靠人记得改。**

NOVICE_DOC = ROOT / "0-先读我-使用说明.txt"


def test_novice_doc_has_no_markdown_markers() -> None:
    """纯文本说明里不能有 markdown 语法——用户看到的是字面的星号。

    新手文档是**第一份**也是唯一一份指引，`**加粗**` 在那里只会变成噪声。
    """
    text = NOVICE_DOC.read_text(encoding="utf-8")
    offenders = [
        (i, ln.strip())
        for i, ln in enumerate(text.splitlines(), 1)
        if "**" in ln
    ]
    assert offenders == [], f"面向新手的纯文本里出现 markdown 星号：{offenders[:3]}"


def test_documented_adr_count_matches_decisions_file() -> None:
    """文档里写死的"X 条 ADR"必须等于 decisions.md 的实际条数。

    这个数字已经漂移过一次（写 18、实际 22）。与其每次加 ADR 都靠人记得改文档，
    不如让测试来提醒——漂移即红。
    """
    import re

    actual = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8").count("\n## ADR-")
    assert actual > 0, "decisions.md 里没找到 ADR"

    checked = 0
    for name in ("README.md", "0-先读我-使用说明.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for m in re.finditer(r"(\d+)\s*条\s*ADR", text):
            claimed = int(m.group(1))
            assert claimed == actual, (
                f"{name} 声称 {claimed} 条 ADR，实际 {actual} 条 —— 数字漂移了"
            )
            checked += 1
    assert checked > 0, "文档里没有出现 ADR 条数声明（若已改成不写死数字，请同步删掉本测试）"


def test_documented_ui_pages_match_actual_nav() -> None:
    """说明书里的页面清单必须与实际导航一致（否则用户按图索骥会找不到）。"""
    import re

    app = (ROOT / "packages/platform/web/src/App.tsx").read_text(encoding="utf-8")
    labels = re.findall(r'to: "[^"]+", label: "([^"]+)"', app)
    assert labels, "未能从 App.tsx 解析出导航标签"

    text = NOVICE_DOC.read_text(encoding="utf-8")
    # 说明书里那句页面清单
    m = re.search(r"Web 界面左侧有七个页面：\s*\n\s*([^\n]+)", text)
    assert m, "说明书里找不到页面清单段落"
    # 只按中点分隔：页面名本身可能含斜杠（"设置 / 引导"），按 `/` 拆会把一个页面
    # 拆成两个——本测试第一版就是这么误报的。
    claimed = [p.strip() for p in m.group(1).split("·") if p.strip()]

    def norm(s: str) -> str:
        return s.replace(" ", "").replace("／", "/")

    missing = [c for c in claimed if not any(norm(c) in norm(lbl) for lbl in labels)]
    assert missing == [], f"说明书列了这些页面，实际导航里没有：{missing}（实际：{labels}）"
    assert len(claimed) == len(labels), (
        f"说明书列了 {len(claimed)} 个页面，实际导航有 {len(labels)} 个：{labels}"
    )


def test_novice_doc_sorts_before_developer_files() -> None:
    """面向新手的说明必须排在文件列表**最前**。

    这条守的是一个很容易被忽略的新手体验：Windows 资源管理器把中文名排在
    所有 ASCII 名之后，所以原名 `使用说明.txt` 实际显示在列表**最底下**——
    "先读我"的文件反而是最后一个被看到的。因此加 `0-` 前缀让它排到最前。
    """
    doc = NOVICE_DOC.name
    siblings = [
        p.name
        for p in ROOT.iterdir()
        if p.is_file() and not p.name.startswith(".")
    ]
    ordered = sorted(siblings, key=lambda s: s.lower())
    assert ordered[0] == doc, (
        f"第一个显示给用户的文件是 {ordered[0]!r}，而不是新手说明 {doc!r}。"
        "若改了文件名，请保持它能排到最前（数字或符号前缀）。"
    )
