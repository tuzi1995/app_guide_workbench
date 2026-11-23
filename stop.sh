#!/bin/bash
# APP 操作指引可视化工作台 —— 停止脚本
#
# 用法：
#   cd /Users/tuzi/个人项目/One/app_guide_workbench
#   ./stop.sh

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "[停止] 前端服务..."
pkill -f "app_guide_workbench/frontend/node_modules/.bin/vite" 2>/dev/null && echo "         已停止" || echo "         未在运行"

echo "[停止] 后端服务..."
pkill -f "python app.py" 2>/dev/null && echo "         已停止" || echo "         未在运行"

rm -f backend/backend.pid frontend/frontend.pid
echo ""
echo "全部服务已停止。画布数据和已入库的知识库条目不受影响。"
