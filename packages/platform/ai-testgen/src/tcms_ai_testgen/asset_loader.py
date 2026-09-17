"""真实资产加载器（P1）：解析 tcms-can-test 的 DBC 与场景 YAML。

输入（只读，不 import 上游包）：
    * DBC 文件（``BO_`` / ``SG_`` / ``BU_`` / ``VAL_`` / ``BA_`` 子集，支持本仓库
      实测的 tcms.dbc 格式：位序 LSB、无多路复用、标准帧 ID）；
    * 场景 YAML（三种步骤写法：``inject:`` / ``recover:`` / 事件式 ``action:``，
      与上游 tcms/scenarios.py 语义对齐）。

设计约定（文档化于 docs/asset-loader.md）：
    * 文件级错误（不存在 / 空 / 坏 YAML / 编码异常）不抛异常：记入
      ``AssetBundle.bad_files`` + ``AssetIndexStats``，诚实统计 parse_rate；
    * DBC 行级错误：记入 ``DbcFile.raw_line_errors``，不丢弃整文件——刻意残缺
      样本让「解析率」口径有意义（docs/metrics.md）；
    * 一个场景文件 = 一个场景（目录含非场景 YAML 时按文件级容错处理）。

对外入口：
    ``load_dbc(path)`` / ``load_scenario(path)`` / ``load_assets(dbc_dir, scenario_dir)``
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

import yaml

from tcms_ai_testgen.asset_models import (
    AssetBundle,
    AssetIndexStats,
    DbcFile,
    DbcMessage,
    DbcSignal,
    DbcValueDescription,
    ScenarioAsset,
    ScenarioStep,
)

#: DBC 报文/信号定义行（按出现顺序依赖：BO_ 先于其 SG_）
_RE_BO = re.compile(r"^\s*BO_\s+(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\d+)\s+(\S+)")
_RE_SG = re.compile(
    r"^\s*SG_\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*"
    r"\((-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?),(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\)\s*"
    r"\[(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\]"
)
_RE_VAL = re.compile(r"^\s*VAL_\s+(\d+)\s+([A-Za-z_][A-Za-z0-9_]*)")
#: BA_ 报文属性行：周期（ms）/ 发送类型
_RE_BA_CYCLE = re.compile(r'^\s*BA_\s+"GenMsgCycleTime"\s+BO_\s+(\d+)\s+(\d+)\s*;')
_RE_BA_TYPE = re.compile(r'^\s*BA_\s+"GenMsgSendType"\s+BO_\s+(\d+)\s+"([a-z]+)"\s*;')
#: 可选列：单位引号串 + 节点（无引号则跳过）
_RE_UNIT = re.compile(r'"([^"]*)"\s*$')


def load_dbc(path: Union[str, os.PathLike]) -> DbcFile:
    """解析一个 DBC 文件为结构化模型。

    文件不存在 / 空 / 读取失败时抛 ``FileNotFoundError`` / ``ValueError``
    （统一由 load_assets 层兜底为 bad_files）；行级坏行只记录不中断。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"DBC 文件不存在: {p}")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"DBC 编码异常: {exc}") from exc

    result = _parse_dbc_lines(p, lines)
    return result


def _parse_dbc_lines(p: Path, lines: list[str]) -> DbcFile:
    """把 DBC 文本行解析成 DbcFile（行级错误进 raw_line_errors）。"""
    result = DbcFile(source_path=str(p))
    msg: Optional[DbcMessage] = None
    #: BA_ 报文属性暂存（BO_ 定义在前、BA_ 在后，收尾统一回填）
    cycle_by_id: dict[int, int] = {}
    type_by_id: dict[int, str] = {}
    #: 行号（1-based，仅记录坏行用）
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#")):
            continue
        # 版本行
        if stripped.startswith("VERSION"):
            result.version = stripped[len("VERSION") :].strip().strip('"')
            continue
        # 节点表 BU_:（真实 DBC 头带冒号；NS_ 属性表里的 BU_SG_REL_ 等不带冒号，须排除）
        if stripped.startswith("BU_:"):
            nodes = stripped[len("BU_:") :].strip()
            if nodes:
                result.nodes.extend(n for n in nodes.split() if n)
            continue
        # 报文行
        m = _RE_BO.match(line)
        if m:
            frame_id, name, dlc, tx = m.groups()
            try:
                fid = int(frame_id)
            except ValueError:
                result.raw_line_errors.append(f"{line_no}: BO_ 帧号非整数: {line}")
                continue
            msg = DbcMessage(frame_id=fid, name=name, dlc=int(dlc), transmitter=tx)
            result.messages.append(msg)
            continue
        # 信号行（挂在最近的 BO_ 之下）
        m = _RE_SG.match(line)
        if m and msg is not None:
            (name, start_bit, length, is_signed_str, sign_flag,
             factor_str, offset_str, min_str, max_str) = m.groups()
            try:
                start = int(start_bit)
                length_i = int(length)
            except ValueError:
                result.raw_line_errors.append(f"{line_no}: SG_ 位数非整数: {line}")
                continue
            sig = DbcSignal(
                name=name,
                start_bit=start,
                length=length_i,
                is_signed=(is_signed_str == "1") and (sign_flag == "-"),
                factor=float(factor_str),
                offset=float(offset_str),
                minimum=float(min_str),
                maximum=float(max_str),
                unit=_extract_unit(line),
            )
            msg.signals.append(sig)
            continue
        # 枚举映射
        m = _RE_VAL.match(line)
        if m:
            try:
                fid = int(m.group(1))
                sig_name = m.group(2)
                pairs = _parse_val_pairs(line)
            except (ValueError, IndexError):
                result.raw_line_errors.append(f"{line_no}: VAL_ 无法解析: {line}")
                continue
            result.value_descriptions.setdefault(fid, {})[sig_name] = pairs
            continue
        # 报文属性 BA_（周期 ms / 发送类型）
        m = _RE_BA_CYCLE.match(line)
        if m:
            try:
                cycle_by_id[int(m.group(1))] = int(m.group(2))
            except ValueError:
                result.raw_line_errors.append(f"{line_no}: BA_ 周期非整数: {line}")
            continue
        m = _RE_BA_TYPE.match(line)
        if m:
            type_by_id[int(m.group(1))] = m.group(2)
            continue
        # 声明了 BO_/SG_/VAL_ 头却解析失败的畸形行——诚实记录（不静默吞掉）。
        # 仅限顶格行：NS_ 属性子项（缩进的 VAL_/BA_ 等列表项）不算畸形。
        token = stripped.split(maxsplit=1)[0] if stripped else ""
        if token in {"BO_", "SG_", "VAL_"} and line[0] not in (" ", "\t"):
            hint = "SG_ 出现在报文定义之前" if token == "SG_" and msg is None else "格式无法匹配"
            result.raw_line_errors.append(f"{line_no}: {token} 行{hint}: {line}")
            continue
        # 其它行（NS_/BA_DEF_/BS_/注释/空白/未识别）静默跳过

    # 回填 BA_ 属性（事件型报文的 GenMsgSendType="event"，周期常缺省/0）
    for m in result.messages:
        m.cycle_ms = cycle_by_id.get(m.frame_id)
        if m.frame_id in type_by_id:
            m.send_type = type_by_id[m.frame_id]
    return result


def _extract_unit(line: str) -> str:
    """取信号行尾部双引号单位（无则空串）。"""
    mm = _RE_UNIT.search(line)
    return mm.group(1) if mm else ""


def _parse_val_pairs(line: str) -> dict[int, DbcValueDescription]:
    """把 VAL_ 行 ``123 "label" ... ;`` 解析为 {raw: DbcValueDescription}。

    返回空 dict 表示无有效枚举（文件级 VAL 行不计失败，供下游判断有无映射）。
    """
    out: dict[int, DbcValueDescription] = {}
    body = line.split(";", 1)[0]
    parts = body.split()
    # 移除 "VAL_" fid sig_name 前 3 个 token
    rest = parts[3:]
    i = 0
    while i < len(rest) - 1:
        tok = rest[i]
        if not re.fullmatch(r"-?\d+", tok):
            i += 1  # 容忍脏 token（防御性）
            continue
        raw = int(tok)
        label_raw = rest[i + 1]
        if not (label_raw.startswith('"') and label_raw.endswith('"')):
            i += 1
            continue
        out[raw] = DbcValueDescription(raw_value=raw, label=label_raw[1:-1])
        i += 2
    return out


# ---------------------------------------------------------------------------
# 场景 YAML
# ---------------------------------------------------------------------------

#: 规范化动作（与上游 _VALID_ACTIONS 对齐）
_VALID_ACTIONS = {"inject", "recover"}


def _step_from_yaml(raw: dict) -> Optional[ScenarioStep]:
    """把 YAML 单步归一化为 ScenarioStep；无法识别返回 None（由调用方记录）。"""
    if not isinstance(raw, dict):
        return None
    if "at" not in raw or raw["at"] is None:
        return None
    try:
        ts = float(raw["at"])
    except (TypeError, ValueError):
        return None
    kind: str
    fault: Optional[str] = None
    node: Optional[str] = None
    level: Optional[str] = None
    impact: Optional[str] = None
    expect: Optional[str] = None

    if isinstance(raw.get("inject"), dict):
        kind = "inject"
        inj = raw["inject"]
        fault = str(inj.get("fault", "")) or None
        node = str(inj.get("node", "")) or None
        level = str(inj.get("level", "")) or None
        impact = str(inj.get("impact", "")) or None
        expect = str(inj.get("expect", "")) or None
    elif "recover" in raw:
        kind = "recover"
        fault = str(raw["recover"]) or None
    elif raw.get("action") in _VALID_ACTIONS:
        kind = str(raw["action"])
        fault = str(raw.get("fault", "")) or None
        node = str(raw.get("node", "")) or None
        level = str(raw.get("level", "")) or None
        impact = str(raw.get("impact", "")) or None
        expect = str(raw.get("expect", "")) or None
    else:
        return None  # 无法识别（无 inject/recover/合法 action）——调用方记 skipped
    return ScenarioStep(
        at=ts,
        kind=kind,
        fault=fault,
        node=node,
        level=level,
        impact=impact,
        expect=expect,
        raw=raw,
    )


def load_scenario(path: Union[str, os.PathLike]) -> ScenarioAsset:
    """解析一个场景 YAML 文件。

    文件不存在抛 ``FileNotFoundError``；内容非 dict / 无 steps 列表 / YAML
    语法错抛 ``ValueError``（统一由 load_assets 兜底为 bad_files）。
    个别步骤无法识别时跳过并记录（文件仍算 parsed——半结构也算资产，
    统计口径见 docs/metrics.md）。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"场景文件不存在: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"场景编码异常: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 语法错误: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层不是映射: {type(data).__name__}")
    if "steps" not in data:
        raise ValueError("YAML 缺少 steps 列表")
    steps_raw = data["steps"]
    if not isinstance(steps_raw, list):
        raise ValueError("steps 不是列表")

    steps: list[ScenarioStep] = []
    skipped: list[str] = []
    for i, raw in enumerate(steps_raw, start=1):
        st = _step_from_yaml(raw)
        if st is None:
            skipped.append(f"step#{i} 无法识别: {raw!r}")
            continue
        steps.append(st)

    return ScenarioAsset(
        source_path=str(p),
        name=str(data.get("name", "")) or None,
        steps=steps,
        skipped_steps=skipped,
        raw=data,
    )


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def load_assets(
    dbc_dir: Union[str, os.PathLike],
    scenario_dir: Union[str, os.PathLike],
) -> AssetBundle:
    """加载某仓库的全部资产（DBC + 场景 YAML），坏文件容错返回统计。

    - dbc_dir 不存在 / 无 *.dbc → bad_dbc 增 1 并记录（不抛）
    - 每个坏 YAML → bad_yaml + bad_files 追加
    - 空目录视为「合法空集」不报坏（与坏文件区分，避免噪音）
    """
    dbc_path = Path(dbc_dir)
    scen_path = Path(scenario_dir)
    bundle = AssetBundle(dbc=[], scenarios=[], stats=AssetIndexStats(yaml_count=0, parsed_yaml=0, bad_yaml=0))

    dbc_files: list[Path] = []
    if dbc_path.is_dir():
        dbc_files = sorted(dbc_path.glob("*.dbc"))
    else:
        # 目录缺失视为 1 个坏 DBC（诚实计数，避免 parse_rate 分母失真）
        bundle.stats.bad_dbc += 1
        bundle.bad_files.append(f"DBC 目录不存在: {dbc_path}")

    bundle.stats.dbc_count = len(dbc_files)
    for f in dbc_files:
        try:
            bundle.dbc.append(load_dbc(f))
            bundle.stats.parsed_dbc += 1
        except (FileNotFoundError, ValueError, OSError) as exc:
            bundle.stats.bad_dbc += 1
            bundle.bad_files.append(f"{f.name}: {exc}")

    yaml_files: list[Path] = []
    if scen_path.is_dir():
        yaml_files = sorted(scen_path.glob("*.yaml"))
    else:
        bundle.bad_files.append(f"场景目录不存在: {scen_path}")

    bundle.stats.yaml_count = len(yaml_files)
    for f in yaml_files:
        try:
            sc = load_scenario(f)
            bundle.scenarios.append(sc)
            bundle.stats.parsed_yaml += 1
        except (FileNotFoundError, ValueError, OSError) as exc:
            bundle.stats.bad_yaml += 1
            bundle.bad_files.append(f"{f.name}: {exc}")

    return bundle


# ---------------------------------------------------------------------------
# 已知真实资产常量（tcms-can-test 仓库相对本包仓库的位置）
# ---------------------------------------------------------------------------

#: 本仓库根目录（src/tcms_ai_testgen/asset_loader.py -> 包根三层）
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent.parent

#: 上游 tcms-can-test 仓库名（与 REPO_ROOT 平级，物理路径可由环境变量覆盖）
UPSTREAM_REPO_DIR_NAME = "tcms-can-test"


def default_upstream_root() -> Path:
    """返回上游仓库根目录的默认猜测。

    优先环境变量 ``TCMS_UPSTREAM_ROOT``，其次 ``REPO_ROOT / tcms-can-test``
    （E:/DSHworkplace/objects/ 平级布局）。供 examples / 后续 P2 使用，
    不存在时由调用方决定是否降级（demo 打印提示，不崩溃）。
    """
    env = os.environ.get("TCMS_UPSTREAM_ROOT")
    if env:
        return Path(env)
    return REPO_ROOT / UPSTREAM_REPO_DIR_NAME


__all__ = [
    "REPO_ROOT",
    "UPSTREAM_REPO_DIR_NAME",
    "default_upstream_root",
    "load_assets",
    "load_dbc",
    "load_scenario",
]
