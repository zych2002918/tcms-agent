# 打包为 Windows exe（可选）

把 tcms-ai-platform 打成**免 Python 环境**的可执行程序，给不会配环境的现场人员/新手用。

## 产物

```
dist/tcms-ai-platform/
  tcms-ai-platform.exe     ← 双击启动（或命令行运行）
  _internal/                ← 运行时依赖 + 内置资产 + 前端静态资源
```

打包后**无需安装 Python / 无需配 TCMS 引擎 / 无需配前端**——引擎(1.9.1)、内置资产
快照、web UI 全部打进包里。启动自动开浏览器到 http://127.0.0.1:8000（`PORT` 环境变量可改）。

## 如何打包（开发者）

前置：已装 Python 3.11 venv，且按下面顺序装好：

```bash
pip install -e .            # 平台本体（editable 即可，spec 用仓库 src 路径）
pip install tcms-can-test   # 引擎（非 editable，普通安装，PyInstaller 才能收全）
pip install pyinstaller
```

然后：

```bash
pyinstaller --noconfirm --clean packaging/tcms-ai-platform.spec
```

产物在 `dist/tcms-ai-platform/`。可把整个目录压缩分发（或再用 Inno Setup 打成单个安装包）。

## spec 做了什么

- 入口 `scripts/exe_entry.py`（绝对导入，避免相对导入在打包态失败）
- `collect_data_files("tcms_ai_platform")` → 内置资产 `_assets/` + 领域 JSON `domain/data/`
- `collect_data_files("tcms")` → 引擎的 `faults.yaml` / `tcms.dbc`（引擎用 `Path(__file__).parent` 定位）
- `web/dist` 拷入 `_MEIPASS/web/dist`（frozen 时 app.py 从这里找前端）
- `console=True`：保留控制台窗口显示启动日志，便于排查；正式发布可改 `False`

## 验证

```bash
dist/tcms-ai-platform/tcms-ai-platform.exe
# → http://127.0.0.1:8000 可用；/api/system/status 显示 engine.ok=true、version=1.9.1
```

## 配置（exe 同样支持外部可配置接口）

- 设置文件：`%USERPROFILE%\.tcms-ai-platform\settings.json`（UI「设置/新手引导」页写入）
- 环境变量：`PORT` / `TCMS_UPSTREAM_DIR` / `DASH_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 等
