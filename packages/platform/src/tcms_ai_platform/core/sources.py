"""资产源解析：让平台在「任意目录 clone」后都能找到资产与引擎。

三级查找（优先级从高到低）：
1. 环境变量 `TCMS_UPSTREAM_DIR`  —— 用户显式指向活的上游 tcms-can-test（开发/演示）
2. 本地兄弟目录 `../tcms-can-test` —— 与平台仓库 clone 在相邻位置（双仓库工作流）
3. 平台内置资产快照 `_assets/`（随 wheel 分发） —— 新人只 clone 平台一个仓库即可跑

引擎发现：
- 场景执行/Agent 需要真实 tcms 引擎（import tcms）。
- 若 TCMS_UPSTREAM_DIR 存在 → 把该目录加入 sys.path 并 import 其 tcms 包；
  否则尝试 import 已安装的 tcms（pip install tcms-can-test）。
- 找不到引擎时：资产浏览/知识图谱仍可用，执行类功能给出清晰引导。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
# 平台仓库根：src/tcms_ai_platform/core/ -> 上溯到仓库根
REPO_ROOT = _THIS_DIR.parents[2]
# 内置资产快照目录（包数据，随 wheel 分发）
ASSETS_DIR = _THIS_DIR.parent / "_assets"

# 上游目录内的关键资产相对路径（tcms-can-test 仓库结构）
DBC_REL = Path("tcms/tcms.dbc")
FAULTS_REL = Path("tcms/faults.yaml")
SCENARIOS_REL = Path("scenarios")
RTM_REL = Path("tests/rtm.csv")
TCMS_PKG_REL = Path("tcms")


@dataclass(frozen=True)
class AssetSource:
    """解析结果：资产从哪读 + 引擎从哪 import。"""

    root: Path | None  # 活上游根（None = 用内置快照）
    mode: str  # "upstream-dir" | "sibling" | "bundled"
    dbc: Path
    faults: Path
    scenarios_dir: Path
    rtm: Path
    engine_available: bool
    engine_hint: str = ""


def _engine_importable(extra_path: Path | None = None) -> bool:
    """探测 tcms 引擎是否可 import（可选把上游目录临时加入 sys.path）。"""
    if extra_path is not None and str(extra_path) not in sys.path:
        try:
            # 不真正改动全局：用 spec 探测
            import importlib.util

            pkg_init = extra_path / "tcms" / "__init__.py"
            if pkg_init.is_file():
                spec = importlib.util.spec_from_file_location("tcms", pkg_init)
                return spec is not None
        except Exception:
            return False
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("tcms") is not None
    except Exception:
        return False


def resolve_asset_source() -> AssetSource:
    """解析当前环境的资产源（供 loader 与 server 使用）。

    优先级：
    0. 用户本地设置 ~/.tcms-ai-platform/settings.json 的 asset_dir（现场人员经设置页写入，
       可指向自定义的 tcms-can-test：加自己的故障/场景/用例）→ mode="user-settings"
    1. 显式环境变量 TCMS_UPSTREAM_DIR
    2. 兄弟目录（开发态：与 tcms-can-test 相邻 clone）
    3. 内置快照（默认，随 wheel 分发）
    """
    # 0. 用户设置的自定义资产目录（外接实际资产/用例/故障）
    try:
        from .settings import asset_dir as _settings_asset_dir

        _usr = _settings_asset_dir()
    except Exception:  # noqa: BLE001 - 设置层不可用跳过
        _usr = None
    if _usr:
        root = Path(_usr).expanduser()
        dbc = root / DBC_REL
        if dbc.is_file():
            return AssetSource(
                root=root,
                mode="user-settings",
                dbc=dbc,
                faults=root / FAULTS_REL,
                scenarios_dir=root / SCENARIOS_REL,
                rtm=root / RTM_REL,
                engine_available=_engine_importable(root),
                engine_hint=f"用户设置资产目录 → {root}",
            )
        # 用户配了但路径无效：不静默降级，抛错让 UI 引导修正
        raise FileNotFoundError(
            f"用户设置的自定义资产目录无效: {root}（缺 {DBC_REL.name}）。"
            "请到「设置」页重新选择 tcms-can-test 目录，或清空资产目录设置。"
        )

    # 1. 显式环境变量
    env_dir = os.environ.get("TCMS_UPSTREAM_DIR")
    if env_dir:
        root = Path(env_dir)
        dbc = root / DBC_REL
        if dbc.is_file():
            return AssetSource(
                root=root,
                mode="upstream-dir",
                dbc=dbc,
                faults=root / FAULTS_REL,
                scenarios_dir=root / SCENARIOS_REL,
                rtm=root / RTM_REL,
                engine_available=_engine_importable(root),
                engine_hint=f"TCMS_UPSTREAM_DIR → {root}",
            )

    # 2. 兄弟目录（开发态：与 tcms-can-test 相邻 clone）
    sibling = REPO_ROOT.parent / "tcms-can-test"
    if (sibling / DBC_REL).is_file():
        return AssetSource(
            root=sibling,
            mode="sibling",
            dbc=sibling / DBC_REL,
            faults=sibling / FAULTS_REL,
            scenarios_dir=sibling / SCENARIOS_REL,
            rtm=sibling / RTM_REL,
            engine_available=True,
            engine_hint=f"兄弟目录 → {sibling}",
        )

    # 3. 内置快照（默认，随 wheel 分发）
    return AssetSource(
        root=None,
        mode="bundled",
        dbc=ASSETS_DIR / "tcms.dbc",
        faults=ASSETS_DIR / "faults.yaml",
        scenarios_dir=ASSETS_DIR / "scenarios",
        rtm=ASSETS_DIR / "tests" / "rtm.csv",
        engine_available=_engine_importable(),
        engine_hint="内置资产快照（引擎需 pip install tcms-can-test）",
    )


def ensure_engine_importable(source: AssetSource) -> bool:
    """确保 tcms 引擎可 import。

    活上游（TCMS_UPSTREAM_DIR / 兄弟目录）：把其根加入 sys.path 并真正 import
    验证——engine_available 只是解析期探测，必须在这里落地。
    无活上游（bundled）：尝试 import 已安装的 tcms（pip install tcms-can-test）。
    返回是否真正可用。
    """
    if source.root is not None:
        root = str(source.root)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import tcms  # noqa: F401

            return True
        except ImportError:
            return False
    try:
        import tcms  # noqa: F401

        return True
    except ImportError:
        return False


def bundled_scenarios_fallback() -> Path | None:
    """内置场景快照目录（当上游 scenarios 缺失/无活上游时用于执行）。

    注意：真实引擎（tcms.scenarios）需要上游目录里的 scenarios；
    若平台用内置快照跑，则引擎与快照必须来自同一 tcms-can-test 版本。
    """
    d = ASSETS_DIR / "scenarios"
    return d if d.is_dir() else None


def upstream_for_engine(source: AssetSource) -> Path | None:
    """返回可传给 tcms.scenarios.run_yaml 的「场景目录所属根」。"""
    if source.root is not None:
        return source.root
    # 内置快照场景存在，但没有活上游根 → 引擎需要能 import 的 tcms
    return None
