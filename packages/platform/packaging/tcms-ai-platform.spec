# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: 打包 tcms-ai-platform 为单目录 exe（含前端静态资源 + TCMS 引擎数据）。
# 用法:
#   cd tcms-ai-platform
#   .venv\Scripts\pyinstaller --noconfirm packaging\tcms-ai-platform.spec
# 产物: dist/tcms-ai-platform/tcms-ai-platform.exe
#
# 打包要点:
#   - 把仓库 src/ 加入 pathex,使 editable 安装也能解析到源码包
#   - collect tcms_ai_platform 的全部 package-data(_assets/*.yaml/.dbc、domain/data/*.json)
#   - 把 web/dist 整个拷进 _MEIPASS/web/dist(frozen 时 app.py 从 _MEIPASS 找)
#   - tcms 引擎(已装进 venv site-packages)自动随 import 收集;其 faults.yaml/dbc
#     经 collect_data_files('tcms') 一并带入(引擎用 Path(__file__).parent 定位)

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# PyInstaller 注入 SPECPATH = spec 文件所在目录；仓库根在其上一级
REPO = Path(SPECPATH).resolve().parent
SRC = (REPO / "src").resolve()

hiddenimports = collect_submodules("tcms_ai_platform")

# 平台包数据(_assets 快照 + 领域 JSON)
datas = collect_data_files("tcms_ai_platform")
# TCMS 引擎数据(faults.yaml / tcms.dbc)
datas += collect_data_files("tcms")
# 前端静态资源 → _MEIPASS/web/dist
web_dist = REPO / "web" / "dist"
if web_dist.is_dir():
    datas.append((str(web_dist), "web/dist"))

# 隐藏导入(引擎各子模块由 tcms/__init__ 显式引用,保险起见全收)
hiddenimports += collect_submodules("tcms")

a = Analysis(
    [str(REPO / "scripts" / "exe_entry.py")],
    pathex=[str(SRC), str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "playwright", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tcms-ai-platform",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台: 新人能看启动日志; 正式可改 False
    disable_windowed_traceback=False,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="tcms-ai-platform",
)
