#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PYTHON="$PROJECT_DIR/backend/.venv/bin/python"

if [ ! -x "$BACKEND_PYTHON" ]; then
  echo "缺少后端虚拟环境，请先在项目目录执行 ./start.sh 完成首次依赖安装。" >&2
  exit 1
fi

if [ ! -x "$PROJECT_DIR/frontend/node_modules/.bin/vite" ]; then
  echo "缺少前端依赖，请先在 frontend 目录执行 npm install。" >&2
  exit 1
fi

cd "$PROJECT_DIR/backend"
"$BACKEND_PYTHON" app.py &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR/frontend"
npm run dev -- --host 127.0.0.1 --port 5178 --strictPort
