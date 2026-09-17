"""monorepo 根 conftest：为全部成员的测试统一解析「上游引擎」位置。

背景：原三仓是平级 clone 布局（`../tcms-can-test`），合并为 monorepo 后引擎位于
`packages/engine`。各成员的历史代码里都存在「猜上游路径」的逻辑（platform 的
`sources.resolve_asset_source`、testgen 的 `asset_loader.default_upstream_root`），
以及少量硬编码相对路径的测试。

这里在**测试会话开始时**统一设置两个环境变量，把上游钉死到 `packages/engine`：
- 生产代码的解析链本就以环境变量为最高优先级 → 立刻命中，不再依赖布局猜测；
- 硬编码路径的测试仍能工作（已同步改为 packages/engine），env 则作为二重保险。

只 setdefault（不覆盖），因此外部显式指定 TCMS_UPSTREAM_DIR 的场合仍然优先。
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_ENGINE = _REPO_ROOT / "packages" / "engine"
_ENGINE_MARKER = _ENGINE / "tcms" / "tcms.dbc"

if _ENGINE_MARKER.is_file():
    os.environ.setdefault("TCMS_UPSTREAM_DIR", str(_ENGINE))
    os.environ.setdefault("TCMS_UPSTREAM_ROOT", str(_ENGINE))
    # 让 `import tcms` 在未安装 editable 的场合也能工作（例如直接跑脚本）
    import sys

    if str(_ENGINE) not in sys.path:
        sys.path.insert(0, str(_ENGINE))
