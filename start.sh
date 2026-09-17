#!/usr/bin/env bash
# ============================================================
#  TCMS × AI  monorepo — 一键启动（Web UI）
#  用法：bash start.sh      （可选：PORT=8001 bash start.sh）
# ============================================================
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# 上游引擎已在本仓内（monorepo），不再需要兄弟目录 clone
export TCMS_UPSTREAM_DIR="$PWD/packages/engine"
export TCMS_UPSTREAM_ROOT="$PWD/packages/engine"

echo ""
echo "  ============================================"
echo "   TCMS × AI  monorepo  —  一键启动"
echo "  ============================================"
echo ""

if ! command -v uv >/dev/null 2>&1; then
  echo "  [X] 未找到 uv，请先安装："
  echo "      curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "      （或 pip install uv）"
  exit 1
fi

echo "  [1/2] 同步工作区（uv sync）..."
uv sync --quiet

echo "  [2/2] 启动服务 → http://127.0.0.1:$PORT"
echo ""
echo "  浏览器将在 2 秒后自动打开；关闭本窗口即停止服务。"
echo ""
( sleep 2 && (command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:$PORT" || open "http://127.0.0.1:$PORT" ) ) &
exec uv run python -m uvicorn tcms_ai_platform.server.app:app --host 127.0.0.1 --port "$PORT"
