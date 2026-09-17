#!/usr/bin/env bash
# TCMS × AI 测试平台 — 一键启动（macOS / Linux）
set -e
cd "$(dirname "$0")"

echo ""
echo "  ============================================"
echo "   TCMS × AI 测试平台  一键启动"
echo "  ============================================"
echo ""

PORT="${PORT:-8000}"

# 1. 虚拟环境
if [ ! -x ".venv/bin/python" ]; then
  echo "  [1/4] 首次运行：创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi
PYV=".venv/bin/python"

# 2. 依赖
if ! "$PYV" -c "import tcms_ai_platform, fastapi, uvicorn, cantools, yaml, numpy" >/dev/null 2>&1; then
  echo "  [2/4] 安装依赖（首次较慢）..."
  "$PYV" -m pip install --upgrade pip -q
  "$PYV" -m pip install -e . -q
else
  echo "  [2/4] 依赖已就绪"
fi

# 3. TCMS 引擎（可选）
if ! "$PYV" -c "import tcms" >/dev/null 2>&1; then
  if [ -n "$TCMS_UPSTREAM_DIR" ]; then
    echo "  [3/4] 使用 TCMS_UPSTREAM_DIR=$TCMS_UPSTREAM_DIR"
  elif [ -d "../tcms-can-test/tcms" ]; then
    echo "  [3/4] 发现兄弟目录 tcms-can-test"
  else
    echo "  [3/4] 未发现 TCMS 引擎，尝试安装（失败不影响资产浏览/知识图谱）..."
    "$PYV" -m pip install -e ".[upstream]" -q || echo "        （可稍后手动: pip install -e \".[upstream]\"）"
  fi
else
  echo "  [3/4] TCMS 引擎已就绪"
fi

# 4. 启动
echo "  [4/4] 启动 → http://127.0.0.1:$PORT"
echo ""
(sleep 2 && (command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:$PORT" || open "http://127.0.0.1:$PORT")) &
"$PYV" -m uvicorn tcms_ai_platform.server.app:app --host 127.0.0.1 --port "$PORT"
