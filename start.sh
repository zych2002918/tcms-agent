#!/usr/bin/env bash
# =============================================================================
#  TCMS × AI —— 一键启动（Web UI，macOS / Linux）
#
#  用法：bash start.sh         （可选：PORT=8001 bash start.sh）
#  新机器上无需事先安装 Python：uv 会自动准备解释器。
# =============================================================================
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# 领域引擎就在本仓内，无需额外配置
export TCMS_UPSTREAM_DIR="$PWD/packages/engine"
export TCMS_UPSTREAM_ROOT="$TCMS_UPSTREAM_DIR"

echo ""
echo "  ============================================================"
echo "    TCMS × AI —— 列车控制软件智能测试平台 + AI 测试工程师 Agent"
echo "  ============================================================"
echo ""

# ---- 1/4 找 uv：PATH -> 包内 _offline/uv -> 联网安装 ----
UV=""
if command -v uv >/dev/null 2>&1; then
  UV="uv"
  echo "  [1/4] 已安装 uv：$(command -v uv)"
elif [ -x "$PWD/_offline/uv" ]; then
  UV="$PWD/_offline/uv"
  echo "  [1/4] 使用包内自带的 uv"
else
  echo "  [1/4] 未找到 uv，尝试联网安装（仅需一次）..."
  if curl -LsSf https://astral.sh/uv/install.sh | sh; then
    UV="$HOME/.local/bin/uv"
    command -v uv >/dev/null 2>&1 && UV="uv"
  fi
fi

if [ -z "$UV" ]; then
  echo ""
  echo "  [X] 无法获得 uv，请任选一种方式后重试："
  echo "      1) 联网后重新运行本脚本（会自动安装 uv）"
  echo "      2) curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "      3) 下载 uv 放到本目录的 _offline/ 子目录下"
  echo "         https://github.com/astral-sh/uv/releases"
  exit 1
fi

# ---- 2/4 同步依赖 ----
echo "  [2/4] 同步依赖（首次运行需数分钟，之后秒开）..."
SYNC_ARGS="sync --quiet"
if [ "$TCMS_OFFLINE" = "1" ] && [ -d "$PWD/_offline/uv-cache" ]; then
  export UV_CACHE_DIR="$PWD/_offline/uv-cache"
  SYNC_ARGS="sync --quiet --offline"
  echo "  [!]  离线模式（使用包内缓存）"
fi
# shellcheck disable=SC2086
if ! $UV $SYNC_ARGS; then
  echo ""
  echo "  [X] 依赖同步失败。常见原因："
  echo "      · 无网络：首次运行需联网下载 Python 与依赖"
  echo "      · 代理问题：确认代理可用，或清除 git/uv 的代理设置"
  exit 1
fi
echo "  [2/4] 依赖就绪"

# ---- 3/4 自检 ----
echo "  [3/4] 环境自检..."
if $UV run python -c "import tcms, tcms_ai_platform, tcms_ai_testgen, tcms_agent" 2>/dev/null; then
  echo "  [3/4] 四个成员包均可导入"
else
  echo "  [!]  成员包导入异常，仍将尝试启动"
fi

# ---- 4/4 启动 ----
echo ""
echo "  [4/4] 启动服务 → http://127.0.0.1:$PORT"
echo "        关闭本终端即停止服务。"
echo ""
( sleep 2 && (command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:$PORT" || open "http://127.0.0.1:$PORT") ) &
exec $UV run python -m uvicorn tcms_ai_platform.server.app:app --host 127.0.0.1 --port "$PORT"
