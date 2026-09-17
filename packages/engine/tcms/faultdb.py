"""统一故障字典（Fault Dictionary / FMEA 注册表）—— 系统级故障管理的数据根基。

对标真实轨道交通系统级测试流程：整车/子系统测试在开展前先建立
**FMEA / 故障字典**，为每个故障登记完整档案——故障来源（所属子系统/
注入层）、注入方法、检测手段、期望系统响应、恢复方法、SIL 关联。
测试用例按故障 ID（F-TCMS-xxx）引用字典条目，注入与检测/判定解耦，
保证"故障 → 检测 → 处置 → 恢复"整条证据链可追溯（EN 50128 追溯思想）。

本模块：
    - 从 `tcms/faults.yaml` 加载故障字典（唯一数据源）
    - 校验字典完整性：fid/key 唯一、level 与 faultlevel 对齐、字段齐全
    - 提供查询 API：by_key / by_fid / by_level / by_subsystem / by_sil / report
    - 与 `faultlevel.FAULTS` 的**双源一致性检查**：重名故障的等级必须一致，
      防止"字典说 major、分级表说 critical"的漂移（tests 断言）

用法：
    db = load_fault_dictionary()
    db.by_key("overspeed")        # → 字典条目 dict
    db.by_level("critical")       # → [条目...]
    db.by_subsystem("网络")       # → [条目...]
    db.report()                   # {'total': 22, 'by_level': {...}, ...}
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from . import faultlevel

FAULTS_YAML = Path(__file__).resolve().parent / "faults.yaml"

# 字典条目必须包含的字段
REQUIRED_FIELDS = (
    "fid",
    "key",
    "name",
    "subsystem",
    "layer",
    "level",
    "action",
    "sil",
    "desc",
    "detect",
    "inject",
    "recovery",
)

# 允许的注入层
VALID_LAYERS = ("application", "signal", "frame", "bus", "node")

# 字典中与 faultlevel.FAULTS 重名的键（双源一致性检查范围）
_FAULTLEVEL_KEYS = frozenset(faultlevel.FAULTS)


class FaultDictionaryError(ValueError):
    """故障字典数据不合法（加载/校验失败）。"""


class FaultDictionary:
    """故障字典：加载后的只读查询视图。"""

    def __init__(self, entries: list[dict], path: str | None = None):
        self._entries = list(entries)
        self._source = path
        self._by_key = {e["key"]: e for e in self._entries}
        self._by_fid = {e["fid"]: e for e in self._entries}

    # ---- 查询 ----

    def by_key(self, key: str) -> dict:
        """按英文键名查询（与 faultlevel/场景 YAML 的 fault 字段一致）。"""
        try:
            return dict(self._by_key[key])
        except KeyError:
            raise KeyError(f"故障字典无此键: {key}（已知 {list(self._by_key)}）") from None

    def by_fid(self, fid: str) -> dict:
        """按故障 ID（F-TCMS-xxx）查询。"""
        try:
            return dict(self._by_fid[fid])
        except KeyError:
            raise KeyError(f"故障字典无此 FID: {fid}（已知 {list(self._by_fid)}）") from None

    def by_level(self, level: str) -> list[dict]:
        """按后果等级过滤（info/minor/major/critical）。"""
        return [dict(e) for e in self._entries if e["level"] == level]

    def by_subsystem(self, subsystem: str) -> list[dict]:
        """按子系统过滤（精确匹配中文名）。"""
        return [dict(e) for e in self._entries if e["subsystem"] == subsystem]

    def by_sil(self, sil: str | int) -> list[dict]:
        """按 SIL 等级过滤（'0'..'4'，'0'=非安全功能）。"""
        s = str(sil)
        return [dict(e) for e in self._entries if e["sil"] == s]

    def by_layer(self, layer: str) -> list[dict]:
        """按注入层过滤（application/signal/frame/bus/node）。"""
        return [dict(e) for e in self._entries if e["layer"] == layer]

    def all(self) -> list[dict]:
        """全部条目（深拷贝列表）。"""
        return [dict(e) for e in self._entries]

    def keys(self) -> list[str]:
        """全部故障键名。"""
        return list(self._by_key)

    def fids(self) -> list[str]:
        """全部故障 ID。"""
        return list(self._by_fid)

    # ---- 汇总 ----

    def report(self) -> dict:
        """字典汇总：总数 + 按等级/子系统/SIL/注入层分布。"""
        by_level: dict[str, int] = {}
        by_sub: dict[str, int] = {}
        by_sil: dict[str, int] = {}
        by_layer: dict[str, int] = {}
        for e in self._entries:
            by_level[e["level"]] = by_level.get(e["level"], 0) + 1
            by_sub[e["subsystem"]] = by_sub.get(e["subsystem"], 0) + 1
            by_sil[e["sil"]] = by_sil.get(e["sil"], 0) + 1
            by_layer[e["layer"]] = by_layer.get(e["layer"], 0) + 1
        return {
            "total": len(self._entries),
            "source": self._source,
            "by_level": dict(sorted(by_level.items())),
            "by_subsystem": by_sub,
            "by_sil": dict(sorted(by_sil.items())),
            "by_layer": by_layer,
        }


# ---- 加载与校验 ----


def _validate_entries(entries: list[dict], path: str) -> None:
    """校验条目结构 + 与 faultlevel 的双源一致性。"""
    seen_key: set[str] = set()
    seen_fid: set[str] = set()
    for i, e in enumerate(entries):
        tag = f"{path}[{i}]"
        for field in REQUIRED_FIELDS:
            if not e.get(field):
                raise FaultDictionaryError(f"{tag}: 缺少必填字段 {field!r}")
        if e["key"] in seen_key:
            raise FaultDictionaryError(f"{tag}: 键名重复 {e['key']!r}")
        if e["fid"] in seen_fid:
            raise FaultDictionaryError(f"{tag}: FID 重复 {e['fid']!r}")
        seen_key.add(e["key"])
        seen_fid.add(e["fid"])
        if e["level"] not in faultlevel.VALID_LEVELS:
            raise FaultDictionaryError(
                f"{tag}: 非法等级 {e['level']!r}（合法 {faultlevel.VALID_LEVELS}）"
            )
        if e["action"] not in faultlevel.ACTION_PRIORITY:
            raise FaultDictionaryError(
                f"{tag}: 非法处置动作 {e['action']!r}（合法 {list(faultlevel.ACTION_PRIORITY)}）"
            )
        if e["layer"] not in VALID_LAYERS:
            raise FaultDictionaryError(f"{tag}: 非法注入层 {e['layer']!r}（合法 {VALID_LAYERS}）")
        if not (e["sil"].isdigit() and 0 <= int(e["sil"]) <= 4):
            raise FaultDictionaryError(f"{tag}: SIL 字段须为 '0'~'4'，got {e['sil']!r}")
        # 双源一致性：与 faultlevel.FAULTS 重名的条目，等级必须一致
        fl = faultlevel.FAULTS.get(e["key"])
        if fl is not None and fl["level"] != e["level"]:
            raise FaultDictionaryError(
                f"{tag}: 故障 {e['key']!r} 字典等级 {e['level']!r} 与 "
                f"faultlevel.FAULTS 等级 {fl['level']!r} 不一致（双源漂移）"
            )


@lru_cache(maxsize=1)
def load_fault_dictionary(path: str | Path = FAULTS_YAML) -> FaultDictionary:
    """从 YAML 加载并校验故障字典。

    带 1 项缓存：场景引擎每注入一次都会查询处置动作（faultlife
    回退路径），YAML 解析 + 22 条全字段校验是纯只读开销，缓存后
    热路径（回放链/批量注入）不再重复解析。
    """
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("faults"), list):
        raise FaultDictionaryError(f"{p}: 顶层须为 {{schema_version, faults: [...]}}")
    entries = raw["faults"]
    _validate_entries(entries, str(p))
    return FaultDictionary(entries, path=str(p))


def check_faultlevel_alignment() -> list[str]:
    """核对 faultlevel.FAULTS 与字典的键集合差异（报告用，非抛错）。

    返回 faultlevel 有而字典缺的键（字典是超集时为空列表）。
    """
    d = load_fault_dictionary()
    return [k for k in _FAULTLEVEL_KEYS if k not in d.keys()]


def describe(fault_key: str) -> str:
    """面向报告/日志的一句话故障说明（key: name（SIL x · level））。"""
    e = load_fault_dictionary().by_key(fault_key)
    return f"{e['key']}: {e['name']}（SIL {e['sil']} · {e['level']} · {e['subsystem']}）"
