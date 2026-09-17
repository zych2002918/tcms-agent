"""examples/ 完整性验证：.asc 样例可解析且剧情断言可复现。

防止示例文件被无意改动破坏（示例也是可执行证据链）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
ASC = EXAMPLES / "demo_trip.asc"


def test_example_asc_exists_and_parses():
    assert ASC.exists(), "examples/demo_trip.asc 缺失（先跑 examples/make_demo_asc.py）"
    from tcms import canlog

    frames = canlog.parse_asc_file(str(ASC))
    assert len(frames) >= 100
    ids = {f["arb_id"] for f in frames}
    assert ids >= {0x100, 0x200, 0x400}  # 心跳/车速/门


@pytest.mark.smoke
def test_replay_demo_script_passes():
    """replay_demo.py 剧情断言全过（子进程运行，防 import 污染）。"""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(EXAMPLES / "replay_demo.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"回放演示断言失败:\n{proc.stdout}\n{proc.stderr}"
    assert "全部剧情断言通过" in proc.stdout


@pytest.mark.smoke
def test_consumer_api_script_passes():
    """第二消费者示例：公共 API 面自证（version/dbc/faults/bus/scene/replay）。"""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(EXAMPLES / "consumer_api.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"消费者 API 示例失败:\n{proc.stdout}\n{proc.stderr}"
    assert "全部自证通过" in proc.stdout
    assert "tcms.__version__" in proc.stdout or "version=1.9" in proc.stdout
