#!/bin/bash

echo "==================================="
echo "🚀 启动 VeWealth"
echo "==================================="

# 设置环境变量
export ENV=prod

# 检查配置文件是否存在
if [ ! -f backend/settings/.prod.env ]; then
    echo "❌ 错误: backend/settings/.prod.env 文件不存在"
    exit 1
fi

# 启动服务
docker-compose up -d --build

# 等待服务启动
echo ""
echo "正在等待服务启动..."
sleep 5

# 检查服务状态
docker-compose ps
