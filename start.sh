#!/bin/bash
# APP 操作指引可视化工作台 —— 一键启动脚本
#
# 用法：双击本文件，或在终端里执行
#   cd /Users/tuzi/个人项目/One/app_guide_workbench
#   ./start.sh
#
# 启动后浏览器打开 http://localhost:5178/ 即可使用。
# 停止：在终端里按 Ctrl + C。

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "=============================================="
echo " APP 操作指引可视化工作台"
echo "=============================================="
echo "项目目录：$PROJECT_DIR"
echo ""

# ---------------------------------------------------------------- 检查后端
if [ ! -x "backend/.venv/bin/python" ]; then
  echo "[准备] 正在创建 Python 虚拟环境（第一次启动会慢一点）..."
  (cd backend && uv venv --python 3.12)
  (cd backend && uv pip install -r requirements.txt)
fi

# 端口占用检查（只看「正在监听」的进程，浏览器连上来的不算）
if lsof -tiTCP:8095 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[检查] 8095 端口已被占用，先停掉旧进程..."
  pkill -f "python app.py" || true
  sleep 1
fi

echo "[启动] 后端服务（端口 8095）..."
(cd backend && nohup .venv/bin/python app.py > backend.log 2>&1 &)
echo "         pid 已写入 backend/backend.pid，日志见 backend/backend.log"

sleep 2

# ---------------------------------------------------------------- 检查前端
if lsof -tiTCP:5178 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[检查] 5178 端口已被占用，先停掉旧进程..."
  pkill -f "app_guide_workbench/frontend/node_modules/.bin/vite" || true
  sleep 1
fi

echo "[启动] 前端服务（端口 5178）..."
(cd frontend && nohup npm run dev > frontend.log 2>&1 &)
echo "         日志见 frontend/frontend.log"

sleep 4

echo ""
echo "=============================================="
echo " 启动完成"
echo "=============================================="
echo "工作台地址： http://localhost:5178/"
echo "后端接口：   http://127.0.0.1:8095/api/health"
echo "知识库地址： http://127.0.0.1:8085/   （账号 admin / 密码 123456）"
echo ""
echo "停止服务：打开终端按 Ctrl + C，或执行 ./stop.sh"
echo ""
