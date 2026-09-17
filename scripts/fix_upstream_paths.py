"""把 platform 测试里硬编码的上游路径改为 monorepo 布局。

字节级操作（decode/encode utf-8，不做换行翻译）——避免任何编码/行尾损伤。
用法：uv run python scripts/fix_upstream_paths.py
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "packages" / "platform" / "tests"

OLD = 'parents[2] / "tcms-can-test"'
NEW = 'parents[2] / "engine"  # monorepo: packages/engine'


def main() -> int:
    if not TESTS.is_dir():
        print(f"[X] 目录不存在: {TESTS}")
        return 1
    changed: list[str] = []
    for f in sorted(TESTS.glob("*.py")):
        raw = f.read_bytes()
        text = raw.decode("utf-8")
        if OLD not in text:
            continue
        f.write_bytes(text.replace(OLD, NEW).encode("utf-8"))
        changed.append(f.name)
    print(f"[OK] 修改 {len(changed)} 个文件")
    for n in changed:
        print(f"     - {n}")
    # 自证：不残留旧串，且新文件仍是合法 UTF-8 且无 BOM
    left = 0
    bad_bom = 0
    for f in sorted(TESTS.glob("*.py")):
        raw = f.read_bytes()
        if OLD.encode() in raw:
            left += 1
        if raw.startswith(b"\xef\xbb\xbf"):
            bad_bom += 1
        raw.decode("utf-8")  # 解码失败会抛错 = 自证
    print(f"[自证] 残留旧路径: {left} | 带 BOM 文件: {bad_bom}")
    return 0 if left == 0 and bad_bom == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
