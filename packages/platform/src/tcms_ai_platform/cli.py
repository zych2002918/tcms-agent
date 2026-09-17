"""tcms-ai-platform 自检 CLI（--doctor）。

让新人在启动前/启动失败后一键知道：
- 资产源用的是哪个（活上游 / 兄弟目录 / 内置快照）
- TCMS 引擎是否可用（场景执行/Agent 的前提）
- 依赖是否齐、端口是否被占
- LLM key 是否配置（可选：Mock 后端离线可用，无需 key）
"""

from __future__ import annotations

import os
import socket
import sys

_failures: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    mark = "[OK]" if ok else "[!!]"
    print(f"  {mark} {name}" + (f"  - {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def doctor() -> int:
    # 兼容 GBK/非 UTF-8 控制台：避免 ✓/emoji 等符号触发 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 - 重配失败不影响主流程
        pass
    print("\n=== tcms-ai-platform 自检 ===\n")

    # Python
    _check("Python ≥3.11", sys.version_info >= (3, 11), f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    # 关键依赖
    deps = {"fastapi": "fastapi", "uvicorn": "uvicorn", "cantools": "cantools", "yaml": "yaml", "numpy": "numpy"}
    for name, mod in deps.items():
        try:
            __import__(mod)
            _check(f"依赖 {name}", True)
        except ImportError:
            _check(f"依赖 {name}", False, "缺 → 先 pip install -e .")

    # 资产源
    try:
        from .core.sources import resolve_asset_source

        src = resolve_asset_source()
        ok_assets = src.dbc.is_file() and src.faults.is_file() and src.scenarios_dir.is_dir() and src.rtm.is_file()
        _check("资产源", ok_assets, f"mode={src.mode} | {src.engine_hint}")
        if not ok_assets:
            _check("  资产文件", False, "内置快照缺失 → 重新安装/检查 src/tcms_ai_platform/_assets")
    except Exception as e:  # noqa: BLE001
        _check("资产源解析", False, str(e))
        src = None

    # 引擎
    if src is not None and src.engine_available:
        _check("TCMS 引擎", True, "import tcms 可用 → 场景执行/Agent 可用")
    else:
        _check(
            "TCMS 引擎",
            False,
            "不可用 → 场景执行/Agent 会提示。修复：pip install -e '.[upstream]' 或设 TCMS_UPSTREAM_DIR",
        )

    # LLM key（可选）
    has_key = bool(os.environ.get("DASH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY"))
    _check("LLM key", True, "已配置（可选）" if has_key else "未配置 — 离线 Mock 可用，当前无需 key")

    # 端口占用
    for port in (8000,):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            busy = s.connect_ex(("127.0.0.1", port)) == 0
        _check(f"端口 {port}", not busy, "被占用 — 先关占用进程或改 PORT 环境变量" if busy else "空闲")

    print(f"\n=== 完成：{len(_failures)} 项需注意 ===\n")
    if _failures:
        print("以下项会影响部分功能：")
        for f in _failures:
            print(f"  - {f}")
        print("\n提示：仅缺 TCMS 引擎 / LLM key 时，资产浏览与知识图谱仍可正常使用。")
    return 1 if _failures else 0


def main() -> None:
    raise SystemExit(doctor())


if __name__ == "__main__":
    main()
