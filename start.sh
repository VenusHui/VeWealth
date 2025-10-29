#!/bin/bash

echo "==================================="
echo "启动 VeWealth A股股票分析平台"
echo "==================================="

# 检查是否在项目根目录
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo "错误: 请在项目根目录运行此脚本"
    exit 1
fi

# 启动后端
echo ""
echo "正在启动后端服务..."
cd backend

# 激活环境并安装依赖
pip install -r requirements.txt > /dev/null 2>&1

# 在后台启动后端
echo "后端服务启动中... (http://localhost:8001)"
python main.py &
BACKEND_PID=$!
cd ..

# 等待后端启动
sleep 3

# 启动前端
echo ""
echo "正在启动前端服务..."
cd frontend

# 检查node_modules
if [ ! -d "node_modules" ]; then
    echo "安装前端依赖..."
    npm install
fi

echo "前端服务启动中... (http://localhost:3000)"
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "==================================="
echo "✅ 服务启动成功！"
echo "==================================="
echo "后端API: http://localhost:8001"
echo "API文档: http://localhost:8001/docs"
echo "前端应用: http://localhost:3000"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo "==================================="

# 等待用户中断
trap "echo ''; echo '停止服务...'; kill $BACKEND_PID $FRONTEND_PID; exit" INT
wait

