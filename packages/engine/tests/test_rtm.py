"""RTM 追溯矩阵校验测试（tests/rtm.csv）。

领域审计 P0-4 落地：需求追溯矩阵可执行化。校验：
    - CSV 可解析、字段齐全
    - 每个 (module, test_file) 文件真实存在
    - SR-01~52 全部被追溯（status=covered 或显式 uncovered）
    - 无重复 (req_id, module) 行（防手抄漂移）
"""

import csv
from pathlib import Path

RTM_CSV = Path(__file__).resolve().parent / "rtm.csv"

# safety_case.md 定义的全部 SR（手工核对；新增 SR 必须同步此处与 rtm.csv）
ALL_SR = [f"SR-{i:02d}" for i in range(1, 53)]

REPO_ROOT = RTM_CSV.parent.parent


def _rows() -> list[dict]:
    with open(RTM_CSV, "r", encoding="utf-8") as f:
        # 跳过 # 注释行（DictReader 不支持原生注释）
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def test_rtm_parseable_and_fields():
    rows = _rows()
    assert rows, "rtm.csv 为空"
    for r in rows:
        for field in ("req_id", "module", "test_file", "verifies", "status"):
            assert r[field].strip(), f"缺字段 {field}: {r}"


def test_rtm_referenced_files_exist():
    """表内 module 与 test_file 必须真实存在于仓库。"""
    for r in _rows():
        module = REPO_ROOT / r["module"]
        test = REPO_ROOT / r["test_file"]
        assert module.exists(), f"{r['req_id']} 引用不存在的模块 {r['module']}"
        assert test.exists(), f"{r['req_id']} 引用不存在的测试 {r['test_file']}"


def test_rtm_covers_all_srs():
    """SR-01~52 全部被追溯（covered 或显式 uncovered）。"""
    covered = {r["req_id"] for r in _rows()}
    missing = [sr for sr in ALL_SR if sr not in covered]
    assert not missing, f"未被 RTM 追溯的 SR: {missing}"


def test_rtm_no_duplicate_req_module_pairs():
    pairs = [(r["req_id"], r["module"]) for r in _rows()]
    assert len(pairs) == len(set(pairs)), "存在重复 (SR, 模块) 行"


def test_rtm_status_values():
    for r in _rows():
        assert r["status"] in ("covered", "uncovered", "partial"), r


def test_rtm_sr_ids_valid_format():
    import re

    for r in _rows():
        assert re.fullmatch(r"SR-\d{2}", r["req_id"]), r
