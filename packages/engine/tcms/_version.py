"""版本号单一真源（Single Source of Truth）。

pyproject.toml 的 ``[project] dynamic = ["version"]`` 与本模块共享此常量，
保证"仓库代码看到的版本 == 发布包的版本"，杜绝手工双写漂移。

发布新版本时：只改这里 + CHANGELOG.md（发布流程见 CONTRIBUTING.md）。
"""

__version__ = "1.12.0"
