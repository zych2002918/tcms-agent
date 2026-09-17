"""oracle ↔ 上游 faultlevel 对拍测试（防双源漂移，red-team 高1）。

oracle.py 是上游 faultlevel.FAULTS 的静态镜像。镜像会漂移——本测试在
上游 tcms-can-test 可用时，用 subprocess 读上游真实常量与镜像逐键对比：
等级不一致 / 缺键 → FAIL。上游不可用则 skip（CI 会 checkout 上游，故 CI 必跑）。

注意：镜像只覆盖 auto 模式一列；上游 rm 模式等差异属已知边界（记录于
docs/decisions.md），此处只对拍「auto 模式等级 + 键集合」这一镜像承诺面。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tcms_ai_testgen.asset_loader import default_upstream_root
from tcms_ai_testgen.oracle import FAULT_LEVELS, LEVEL_ACTION, fault_keys

_SCRIPT = """\
import json, sys
sys.path.insert(0, r"{root}")
from tcms import faultlevel
out = {{"keys": list(faultlevel.FAULTS), "levels": {{k: v["level"] for k, v in faultlevel.FAULTS.items()}},
       "level_action": dict(faultlevel.LEVEL_ACTION)}}
print(json.dumps(out))
"""


def _upstream_faultlevel(root: Path) -> dict:
    """subprocess 读上游真实 faultlevel（避免 import 污染本进程）。"""
    py = root / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = None
    code = _SCRIPT.format(root=root)
    proc = subprocess.run(
        [str(py) if py else sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"上游 faultlevel 读取失败: {proc.stderr[-500:]}")
    return json_loads(proc.stdout)


def json_loads(s: str) -> dict:
    import json

    return json.loads(s)


@pytest.fixture(scope="module")
def upstream_fl():
    root = default_upstream_root()
    if not (root / "tcms" / "faultlevel.py").is_file():
        pytest.skip(f"上游仓库不可用: {root}")
    return _upstream_faultlevel(root)


class TestOracleAlignment:
    def test_key_set_matches(self, upstream_fl: dict) -> None:
        """镜像键集合 = 上游键集合（无缺键无多余）。"""
        mirror = set(fault_keys())
        upstream = set(upstream_fl["keys"])
        assert mirror == upstream, f"镜像缺 {upstream - mirror} / 多余 {mirror - upstream}"

    def test_levels_match_auto(self, upstream_fl: dict) -> None:
        """每键等级与上游一致（auto 模式承诺面）。"""
        upstream_levels = upstream_fl["levels"]
        drift = {k: (FAULT_LEVELS.get(k), upstream_levels[k])
                 for k in upstream_levels if FAULT_LEVELS.get(k) != upstream_levels[k]}
        assert not drift, f"等级漂移: {drift}"

    def test_level_action_map_matches(self, upstream_fl: dict) -> None:
        """等级→默认处置表与上游 LEVEL_ACTION 一致。"""
        assert LEVEL_ACTION == upstream_fl["level_action"]

    def test_action_derivable_from_level(self) -> None:
        """oracle 每条 action = LEVEL_ACTION[level]（内部一致）。"""
        from tcms_ai_testgen.oracle import lookup

        for k in fault_keys():
            e = lookup(k)
            assert e is not None
            assert e.action == LEVEL_ACTION[e.level]
