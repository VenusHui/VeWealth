#!/bin/bash

# 本地代码质量检查脚本
# 用于在推送代码前进行代码检查，与 CI 流程保持一致

set -e

echo "=========================================="
echo "🔍 本地代码质量检查"
echo "=========================================="

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 检查 Python 环境
echo -e "${YELLOW}🐍 检查 Python 代码...${NC}"
cd backend

if ! command -v conda &> /dev/null; then
    echo -e "${RED}❌ Conda 未安装${NC}"
    exit 1
fi

# 激活 conda 环境
echo "🔧 激活 conda 环境: vewealth"
eval "$(conda shell.bash hook)"
conda activate vewealth

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ 无法激活 conda 环境 'vewealth'${NC}"
    echo "请确保已创建该环境: conda create -n vewealth python=3.12"
    exit 1
fi

# 安装依赖
echo "📦 安装 Python 依赖..."
pip install --quiet --upgrade pip
pip install --quiet black
pip install --quiet -r requirements.txt

# 运行代码格式检查
echo "🎨 运行 Black 格式检查..."
if black --check . 2>&1; then
    echo -e "${GREEN}✅ Black 检查通过${NC}"
else
    echo -e "${YELLOW}⚠️  建议运行 'black .' 格式化代码${NC}"
fi

cd ..

# 检查 Node.js 环境
echo ""
echo -e "${YELLOW}📦 检查 Node.js/TypeScript 代码...${NC}"
cd frontend

if ! command -v node &> /dev/null; then
    echo -e "${RED}❌ Node.js 未安装${NC}"
    exit 1
fi

# 检查并安装依赖
if [ ! -d "node_modules" ]; then
    echo "📦 安装 Node.js 依赖..."
    npm ci
fi

# 运行 ESLint
echo "🔍 运行 ESLint 检查..."
if npm run lint; then
    echo -e "${GREEN}✅ ESLint 检查通过${NC}"
else
    echo -e "${YELLOW}⚠️  ESLint 检查有警告，但继续执行${NC}"
    # 不退出，允许有警告
fi

cd ..

echo ""
echo "=========================================="
echo -e "${GREEN}✅ 所有代码质量检查通过！${NC}"
echo "=========================================="
echo ""
echo "你可以安全地推送代码到 GitHub"

