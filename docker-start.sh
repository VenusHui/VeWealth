#!/bin/bash

# VeWealth Docker 快速启动脚本

echo "==================================="
echo "启动 VeWealth A股股票分析平台 (Docker)"
echo "==================================="

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: Docker 未安装"
    echo "请访问 https://docs.docker.com/get-docker/ 安装 Docker"
    exit 1
fi

# 检查 Docker Compose 是否安装
if ! command -v docker-compose &> /dev/null; then
    echo "❌ 错误: Docker Compose 未安装"
    echo "请访问 https://docs.docker.com/compose/install/ 安装 Docker Compose"
    exit 1
fi

# 检查 .env 文件是否存在
if [ ! -f .env ]; then
    echo "⚠️  警告: .env 文件不存在"
    echo "正在从 env.example 创建 .env 文件..."
    cp env.example .env
    echo "✅ 已创建 .env 文件"
    echo ""
    echo "⚠️  请编辑 .env 文件，配置必要的环境变量："
    echo "   - POSTGRES_PASSWORD"
    echo "   - SECRET_KEY"
    echo "   - MASTER_KEY"
    echo "   - 微信公众号配置（如需使用）"
    echo ""
    read -p "是否现在编辑 .env 文件? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ${EDITOR:-vim} .env
    fi
fi

echo ""
echo "正在启动服务..."
echo ""

# 构建并启动所有服务
docker-compose up -d --build

# 检查服务状态
echo ""
echo "正在检查服务状态..."
sleep 5

docker-compose ps

echo ""
echo "==================================="
echo "✅ 服务启动成功！"
echo "==================================="
echo "前端应用: http://localhost:3000"
echo "后端API: http://localhost:8001"
echo "API文档: http://localhost:8001/docs"
echo ""
echo "查看日志: docker-compose logs -f"
echo "停止服务: docker-compose down"
echo "==================================="

