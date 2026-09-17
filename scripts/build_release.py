"""打「新电脑点击即用」压缩包。

设计要点（都是踩过坑之后定的）：

1. **用 `git ls-files` 取清单**：只含跟踪文件，天然排除 .venv / node_modules /
   __pycache__ / .git，不会把垃圾打进包里。
2. **逐扩展名控制行尾**：Windows 脚本（.bat/.cmd/.ps1）一律 CRLF，
   shell 脚本 LF。`git archive` 在本机 shell 里不可靠（tar 管道报错），
   且工作区当时仍是 LF，所以在这里显式转换 —— 可控且可验证。
3. **保留 .ps1 的 UTF-8 BOM**：转行尾时若整体 decode/encode 容易丢 BOM，
   这里在字节层面替换，BOM 与内容都不动。
4. **附带离线物**：`_offline/uv.exe`（PATH 无 uv 时使用）与可选的
   `_offline/uv-cache/`（设 TCMS_OFFLINE=1 时全程离线安装依赖）。
   它们不进 git（体积大），只在包里。
5. **打包后自检**：断言关键入口存在、.ps1 带 BOM、.bat 是 CRLF、
   四个成员包齐全。

用法：
    uv run python scripts/build_release.py                # 标准包（含 uv.exe）
    uv run python scripts/build_release.py --offline      # 额外带依赖缓存（大）
    uv run python scripts/build_release.py --no-uv        # 不带 uv.exe（最小）
"""

from __future__ import annotations

import argparse
import subprocess
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 这些扩展名在包里必须是 CRLF（Windows 脚本）
CRLF_EXT = {".bat", ".cmd", ".ps1"}
#: 这些必须是 LF
LF_EXT = {".sh"}

#: 打包后必须存在的关键入口
REQUIRED = [
    "start.bat",
    "start.ps1",
    "_tools/common.ps1",
    "agent-demo.bat",
    "test.bat",
    "使用说明.txt",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "packages/engine/tcms/tcms.dbc",
    "packages/engine/tcms/faults.yaml",
    "packages/platform/pyproject.toml",
    "packages/platform/web/dist/index.html",
    "packages/testgen/pyproject.toml",
    "packages/agent/pyproject.toml",
    "docs/ARCHITECTURE.md",
    "docs/decisions.md",
]


def tracked_files() -> list[str]:
    """取跟踪文件清单。

    必须用 `-z`：`git ls-files` 默认受 core.quotePath 影响，会把非 ASCII 路径
    转义成八进制（例如 `使用说明.txt` 变成 `"\\344\\275\\277..."`），
    于是这些文件会被当成「找不到」而**静默漏掉**——本仓第一次打包就是这样
    漏了 `使用说明.txt`，靠包内自检才发现。
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [p.decode("utf-8") for p in out.stdout.split(b"\0") if p]


def normalise_eol(data: bytes, suffix: str) -> bytes:
    """按扩展名规范行尾（字节层面，保留 BOM 与其余字节）。"""
    if suffix in CRLF_EXT:
        # 先把已有的 CRLF 归一成 LF，再统一成 CRLF（避免出现 CRCRLF）
        return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if suffix in LF_EXT:
        return data.replace(b"\r\n", b"\n")
    return data


def build_zip(files: list[str], extra: list[Path], out: Path, prefix: str) -> dict:
    """打 zip。

    **所有条目都放进 prefix/ 这一层顶层文件夹**：否则用户用「解压到当前目录」
    会把 600 多个文件散落到下载目录里，这不是「点击即用」该有的体验。
    prefix 同时也让解压后的目录名自带版本号，便于同时保留多个版本。
    """
    stats = {"files": 0, "bytes": 0, "eol_fixed": 0, "bom_kept": 0, "missing": []}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            src = ROOT / rel
            if not src.is_file():
                stats["missing"].append(rel)
                continue
            raw = src.read_bytes()
            fixed = normalise_eol(raw, src.suffix.lower())
            if fixed != raw:
                stats["eol_fixed"] += 1
            if src.suffix.lower() in CRLF_EXT and fixed.startswith(b"\xef\xbb\xbf"):
                stats["bom_kept"] += 1
            z.writestr(f"{prefix}/{rel}", fixed)
            stats["files"] += 1
            stats["bytes"] += len(fixed)
        for p in extra:
            if not p.is_file():
                continue
            arc = f"{prefix}/{p.relative_to(ROOT).as_posix()}"
            z.write(p, arc)
            stats["files"] += 1
            stats["bytes"] += p.stat().st_size
    return stats


def selfcheck(zip_path: Path, prefix: str) -> list[str]:
    """对**包内实际内容**做自检，而不是对源目录。"""
    problems: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())

        # zip 内必须是单一顶层文件夹（否则解压会散落一地）
        tops = {n.split("/", 1)[0] for n in names}
        if tops != {prefix}:
            problems.append(f"zip 顶层不是单一文件夹 {prefix!r}，实际：{sorted(tops)[:5]}")

        def has(rel: str) -> bool:
            return f"{prefix}/{rel}" in names

        for req in REQUIRED:
            if not has(req):
                problems.append(f"缺少关键文件: {req}")
        for n in names:
            if n.endswith("/"):
                continue
            if n.endswith((".bat", ".cmd")):
                data = z.read(n)
                if b"\r\n" not in data:
                    problems.append(f"{n} 不是 CRLF 行尾")
                if data.count(b"\n") != data.count(b"\r\n"):
                    problems.append(f"{n} 行尾混用")
            if n.endswith(".ps1"):
                data = z.read(n)
                if not data.startswith(b"\xef\xbb\xbf"):
                    problems.append(f"{n} 缺少 UTF-8 BOM（中文会乱码）")
                if data.count(b"\n") != data.count(b"\r\n"):
                    problems.append(f"{n} 行尾混用")
            if n.endswith(".sh"):
                if b"\r\n" in z.read(n):
                    problems.append(f"{n} 应为 LF 行尾")
        for pkg, marker in (
            ("engine", "packages/engine/pyproject.toml"),
            ("platform", "packages/platform/pyproject.toml"),
            ("testgen", "packages/testgen/pyproject.toml"),
            ("agent", "packages/agent/pyproject.toml"),
        ):
            if not has(marker):
                problems.append(f"成员包缺失: {pkg}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="额外带依赖缓存（体积大，可全程离线）")
    ap.add_argument("--no-uv", action="store_true", help="不带内置 uv.exe")
    ap.add_argument("--out", default=None, help="输出目录（默认 dist/）")
    args = ap.parse_args()

    version = "0.1.0"
    _vp = ROOT / "packages" / "agent" / "src" / "tcms_agent" / "_version.py"
    if _vp.is_file():
        for ln in _vp.read_text(encoding="utf-8").splitlines():
            if ln.startswith("__version__"):
                version = ln.split("=")[1].strip().strip('"').strip("'")
                break

    today = date.today().strftime("%Y%m%d")
    stem = f"tcms-agent-v{version}-{today}"
    outdir = Path(args.out) if args.out else (ROOT / "dist")
    outdir.mkdir(parents=True, exist_ok=True)
    zip_path = outdir / f"{stem}.zip"

    files = tracked_files()
    extra: list[Path] = []
    if not args.no_uv:
        uv_exe = ROOT / "_offline" / "uv.exe"
        if uv_exe.is_file():
            extra.append(uv_exe)
        else:
            print(f"[!] 未找到 {uv_exe}，包内将不含内置 uv（脚本会尝试联网安装）")
    if args.offline:
        cache = ROOT / "_offline" / "uv-cache"
        if not cache.is_dir():
            print(f"[!] 未找到 {cache}；请先执行：")
            print("      $env:UV_CACHE_DIR='<repo>\\_offline\\uv-cache'; uv sync")
            return 2
        for p in cache.rglob("*"):
            if p.is_file():
                extra.append(p)

    print(f"[1/3] 打包 {stem}")
    print(f"      跟踪文件 {len(files)} 个；附加离线物 {len(extra)} 个")
    stats = build_zip(files, extra, zip_path, stem)
    print(f"      写入 {stats['files']} 个文件；规范行尾 {stats['eol_fixed']} 个；"
          f"保留 BOM {stats['bom_kept']} 个")
    if stats["missing"]:
        # 跟踪清单里有、磁盘上却没有 —— 必须报出来，不能静默漏
        print(f"[X] 有 {len(stats['missing'])} 个跟踪文件在磁盘上找不到：")
        for m in stats["missing"][:10]:
            print(f"      - {m}")
        return 1

    print("[2/3] 包内自检...")
    problems = selfcheck(zip_path, stem)
    if problems:
        print("[X] 自检未通过：")
        for p in problems:
            print(f"      - {p}")
        return 1
    print("      单一顶层文件夹；关键入口齐全；Windows 脚本 CRLF；ps1 带 BOM；四成员包齐备")

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"[3/3] 完成：{zip_path}")
    print(f"      体积 {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
