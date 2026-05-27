# VeWealth - A股股票智能分析与监控平台 💰

[![CI/CD Pipeline](https://github.com/VenusHui/VeWealth/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/VenusHui/VeWealth/actions/workflows/ci-cd.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Node.js 20](https://img.shields.io/badge/node-20-green.svg)](https://nodejs.org/)

一个基于 FastAPI 和 Next.js 的现代化A股股票数据分析平台，提供实时数据查询、价格分布分析、长期数据存储、智能预警和微信通知等功能。

## ✨ 核心功能

### 📊 股票分析
- **实时数据查询** - 通过 AKShare 获取A股分时数据
- **价格分布分析** - 按价格聚合成交量，直观展示价格分布
- **多峰正态分布拟合** - 使用 GMM（高斯混合模型）拟合价格分布曲线
- **智能阈值可视化** - 可调节阈值滑块，高亮显示概率密度区间
- **双轴图表** - 独立Y轴显示成交量和拟合曲线，避免量纲差异
- **交互式图表** - 支持柱状图、折线图、拟合曲线的独立显隐控制

### 👁️ 监控列表
- **股票监控管理** - 添加关注的股票到个人监控列表
- **自动数据采集** - 定时任务每日自动采集监控股票的分时数据
- **长期数据存储** - 突破 AKShare 5日限制，PostgreSQL 永久保存历史数据
- **个性化设置** - 每只股票可单独设置预警开关和阈值

### ⚡ 智能预警
- **价格预警** - 当股票价格超过正态分布设定阈值时自动触发
- **微信通知** - 通过微信公众号实时推送预警消息
- **防重复通知** - 同一股票1小时内不重复预警
- **交易时段监控** - 交易时间每5分钟检查一次

### 🔐 用户认证
- **主密钥注册** - 需要主密钥才能注册，保护系统安全
- **JWT 认证** - 基于 Token 的无状态认证
- **多用户支持** - 每个用户独立的监控列表和预警设置

## 🛠️ 技术栈

### 后端
- **FastAPI** - 现代化、高性能的 Python Web 框架
- **SQLAlchemy** - ORM 数据库操作
- **PostgreSQL** - 关系型数据库，存储用户数据和历史分时数据
- **APScheduler** - 定时任务调度
- **AKShare** - 免费开源的股票数据接口
- **scikit-learn** - GMM 模型和机器学习
- **wechatpy** - 微信公众号 SDK
- **JWT** - Token 认证

### 前端
- **Next.js 14** - React 框架（App Router）
- **TypeScript** - 类型安全
- **Tailwind CSS** - 现代化 UI 样式
- **Recharts** - 数据可视化图表库
- **Axios** - HTTP 客户端

## 📦 项目结构

```
VeWealth/
├── backend/                    # 后端 FastAPI 项目
│   ├── main.py                # 应用主入口
│   ├── app/
│   │   ├── core/              # 核心配置
│   │   │   ├── config.py      # 应用配置
│   │   │   ├── database.py    # 数据库连接
│   │   │   ├── security.py    # 安全认证
│   │   │   └── deps.py        # 依赖注入
│   │   ├── models/            # 数据库模型
│   │   │   ├── user.py        # 用户模型
│   │   │   ├── watchlist.py   # 监控列表模型
│   │   │   └── stock_data.py  # 股票数据模型
│   │   ├── schemas/           # Pydantic 模型
│   │   │   ├── auth.py        # 认证相关
│   │   │   ├── watchlist.py   # 监控列表相关
│   │   │   └── stock.py       # 股票相关
│   │   ├── routers/           # API 路由
│   │   │   ├── auth.py        # 认证路由
│   │   │   ├── watchlist.py   # 监控列表路由
│   │   │   └── stock.py       # 股票数据路由
│   │   ├── services/          # 业务逻辑
│   │   │   ├── stock_service.py      # 股票服务
│   │   │   ├── data_collector.py     # 数据采集
│   │   │   ├── alert_service.py      # 预警服务
│   │   │   ├── wechat_service.py     # 微信服务
│   │   │   └── scheduler.py          # 定时任务
│   │   └── utils/             # 工具函数
│   │       └── data_processor.py     # 数据处理
│   └── requirements.txt       # Python 依赖
│
├── frontend/                   # 前端 Next.js 项目
│   ├── app/
│   │   ├── page.tsx           # 首页
│   │   ├── layout.tsx         # 根布局
│   │   ├── login/             # 登录页面
│   │   ├── analysis/          # 股票分析页面
│   │   ├── watchlist/         # 监控列表页面
│   │   ├── components/        # React 组件
│   │   │   ├── Navbar.tsx     # 导航栏
│   │   │   └── StockChart.tsx # 股票图表
│   │   └── lib/               # 工具库
│   │       └── auth.ts        # 认证工具
│   ├── package.json
│   └── tailwind.config.js
│
├── README.md
└── LICENSE
```

## 🚀 快速开始

> **💡 提示**：项目已配置 GitHub Actions CI/CD 自动部署。查看 [CI/CD 快速配置指南](.github/QUICK_START.md) 了解如何设置自动部署到云服务器。

### 方式一：使用 Docker（推荐）🐳

最简单的部署方式，自动配置所有服务（后端、前端、数据库）。

#### 环境要求
- Docker 20.10+
- Docker Compose 2.0+

#### 启动步骤

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/VeWealth.git
cd VeWealth

# 2. 配置环境（支持 local 和 prod 两个环境）
# 本地开发环境配置已预设好，可直接使用
# 生产环境需要修改 backend/settings/.prod.env 中的敏感信息

# 3. 启动服务
# 启动本地开发环境（默认）
./docker-start-local.sh

# 或启动生产环境
./docker-start-prod.sh

# 或手动指定环境
ENV=local docker-compose up -d   # 本地环境
ENV=prod docker-compose up -d    # 生产环境
```

服务将在以下地址运行：
- 前端应用：http://localhost:3000
- 后端API：http://localhost:8001
- API文档：http://localhost:8001/docs

**环境配置说明：**
- `backend/settings/.local.env` - 本地开发环境配置
- `backend/settings/.prod.env` - 生产环境配置（需要修改敏感信息）
- 通过 `ENV` 环境变量切换：`ENV=local` 或 `ENV=prod`

---

### 方式二：本地开发部署

#### 环境要求

- Python 3.8+
- Node.js 18+
- PostgreSQL 12+

#### 1. 克隆项目

```bash
git clone https://github.com/yourusername/VeWealth.git
cd VeWealth
```

#### 2. 数据库设置

创建 PostgreSQL 数据库：

```sql
CREATE DATABASE vewealth;
CREATE USER vewealth WITH PASSWORD 'vewealth123';
GRANT ALL PRIVILEGES ON DATABASE vewealth TO vewealth;
```

#### 3. 后端设置

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境（本地开发环境配置已预设好）
# 如需修改，请编辑 backend/settings/.local.env

# 启动后端服务（默认使用 local 环境）
python main.py

# 或指定环境
ENV=local python main.py   # 本地环境
ENV=prod python main.py    # 生产环境
```

后端将在 http://localhost:8001 运行

#### 4. 前端设置

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端将在 http://localhost:3000 运行

## 📝 配置说明

### 多环境配置

本项目支持 **local**（本地开发）和 **prod**（生产环境）两套配置，通过 `ENV` 环境变量切换。

### 后端配置

配置文件位置：
- `backend/settings/.local.env` - 本地开发环境
- `backend/settings/.prod.env` - 生产环境

主要配置项（完整配置请查看 [backend/settings/README.md](./backend/settings/README.md)）：

```bash
# 环境标识
ENV=local  # 或 prod

# 数据库配置
DATABASE_URL=postgresql://user:password@host:port/database

# JWT 配置（⚠️ 生产环境必须修改）
SECRET_KEY=your_secret_key
MASTER_KEY=your_master_key

# 微信公众号配置
WECHAT_APP_ID=your_wechat_app_id
WECHAT_APP_SECRET=your_wechat_app_secret

# 定时任务配置
SCHEDULER_ENABLED=true
DATA_COLLECT_CRON=0 15 * * 1-5          # 每个交易日 15:00 采集数据
ALERT_CHECK_CRON=*/5 9-15 * * 1-5       # 交易时间每 5 分钟检查预警

# CORS 配置（JSON 数组格式）
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
```

**⚠️ 生产环境安全提示：**
1. 使用 `openssl rand -base64 32` 生成强随机密钥
2. 修改所有默认密码和密钥
3. 设置正确的 CORS 域名
4. 保护配置文件权限：`chmod 400 backend/settings/.prod.env`

### 前端配置 (frontend/.env.local)

```bash
NEXT_PUBLIC_API_URL=http://localhost:8001
```

## 📖 使用指南

### 1. 注册账号

1. 访问 http://localhost:3000
2. 点击"登录/注册"
3. 输入用户名和主密钥（默认主密钥需在后端配置文件中设置）
4. 注册成功后自动登录

### 2. 添加监控股票

1. 登录后点击导航栏的"监控列表"
2. 点击"+ 添加股票"
3. 输入股票代码（如：000001）
4. 设置是否启用预警和预警阈值
5. 点击"添加到监控列表"

### 3. 数据采集

系统会在每个交易日 15:00 自动采集监控列表中所有股票的分时数据，无需手动操作。

### 4. 股票分析

1. 点击导航栏的"股票分析"
2. 可以通过搜索功能找到股票并选择
3. 选择日期范围
4. 点击"查询股票数据"
5. 查看价格分布图表和拟合曲线
6. 调节阈值滑块查看不同概率密度区间
7. 可以独立控制柱状图、折线图、拟合曲线的显示

### 5. 价格预警

1. 在监控列表中启用股票的预警功能
2. 在用户设置中绑定微信 OpenID（需要关注公众号）
3. 系统会在交易时间每 5 分钟检查一次
4. 当价格超过设定阈值时，通过微信公众号发送通知

## 🎯 核心算法

### 多峰正态分布拟合 (GMM)

系统使用高斯混合模型（Gaussian Mixture Model）对价格-成交量分布进行拟合：

1. **数据准备**：将分时数据按价格聚合，得到每个价格点的总成交量
2. **模型选择**：通过 BIC（贝叶斯信息准则）自动选择最优的峰数（2-5个峰）
3. **拟合计算**：使用 EM 算法拟合多峰正态分布
4. **曲线生成**：在价格范围内生成拟合曲线点
5. **阈值判断**：计算当前价格在分布中的密度百分位，与设定阈值比较

## 📊 数据模型

### 用户表 (users)
- id: 主键
- username: 用户名（唯一）
- wechat_openid: 微信 OpenID
- is_active: 是否激活
- alert_threshold: 默认预警阈值
- created_at / updated_at: 时间戳

### 监控列表表 (watchlists)
- id: 主键
- user_id: 用户 ID（外键）
- stock_code: 股票代码
- stock_name: 股票名称
- alert_enabled: 是否启用预警
- alert_threshold: 个性化预警阈值
- last_alerted_at: 最后预警时间
- created_at / updated_at: 时间戳

### 股票分时数据表 (stock_minute_data)
- id: 主键
- stock_code: 股票代码（索引）
- trade_date: 交易日期（索引）
- trade_time: 交易时间（索引）
- open_price / high_price / low_price / close_price: OHLC
- volume: 成交量
- created_at: 创建时间

## 🔧 API 接口

### 认证相关
- `POST /api/auth/register` - 用户注册
- `GET /api/auth/me` - 获取当前用户信息
- `PUT /api/auth/me` - 更新用户信息

### 监控列表
- `GET /api/watchlist` - 获取监控列表
- `POST /api/watchlist` - 添加股票
- `PUT /api/watchlist/{id}` - 更新监控项
- `DELETE /api/watchlist/{id}` - 删除监控项

### 股票数据
- `GET /api/stock/search` - 搜索股票
- `GET /api/stock/data` - 获取股票数据

详细 API 文档：http://localhost:8001/docs

## 🎨 界面预览

### 首页
- 功能介绍
- 技术栈展示
- 快速导航

### 股票分析页面
- 股票搜索
- 日期范围选择
- 交互式图表
- 拟合曲线控制

### 监控列表页面
- 添加/删除股票
- 预警设置
- 状态管理

## 🧾 静态A股股票池维护（回测兜底）

当实时股票列表接口不可用时，系统会回退到 `backend/data/a_share_symbols.txt`。

- 一次性刷新（当前全量）
```bash
python backend/scripts/refresh_a_share_symbols.py
```
- 建议每周刷新（示例：每周日 08:00）
```bash
0 8 * * 0 cd /home/ubuntu/repo/VeWealth && /usr/bin/python3 backend/scripts/refresh_a_share_symbols.py >> backend/logs/symbol_refresh.log 2>&1
```

## 📈 定时任务

### 数据采集任务
- **执行时间**：每个交易日 15:00
- **执行内容**：采集所有监控列表中股票的当日分时数据
- **Cron 表达式**：`0 15 * * 1-5`

### 价格预警任务
- **执行时间**：交易时间（9:00-15:00）每 5 分钟
- **执行内容**：检查所有启用预警的股票价格，触发预警通知
- **Cron 表达式**：`*/5 9-15 * * 1-5`

## 🔒 安全性

- JWT Token 认证保护 API
- 主密钥限制用户注册
- 密码使用 bcrypt 哈希
- SQL 注入防护（SQLAlchemy ORM）
- CORS 跨域保护

## 🐛 故障排除

### 后端启动失败
- 检查 PostgreSQL 是否运行
- 检查数据库连接配置是否正确
- 检查 Python 版本和依赖是否安装完整

### 前端无法连接后端
- 检查后端是否正常运行
- 检查 NEXT_PUBLIC_API_URL 配置
- 检查 CORS 配置

### 微信通知未收到
- 检查微信公众号配置是否正确
- 检查用户是否绑定了微信 OpenID
- 检查模板消息 ID 是否配置

### 定时任务未执行
- 检查 SCHEDULER_ENABLED 是否为 true
- 检查 Cron 表达式是否正确
- 查看后端日志确认任务状态

## 🚢 部署指南

### 自动部署（GitHub Actions CI/CD）

本项目已配置完整的 CI/CD 流程，支持自动构建和部署到云服务器。

- **[快速配置指南](.github/QUICK_START.md)** - 5 分钟快速设置
- **[完整部署文档](.github/DEPLOYMENT.md)** - 详细配置、故障排除、监控等

**工作流程**：
1. 推送代码到 `main` 或 `dev/v1.0.0` 分支
2. 自动执行代码检查和构建测试
3. 通过 SSH 自动部署到服务器
4. 执行健康检查确保服务正常

**支持的部署分支**：
- `main` - 生产环境
- `dev/v1.0.0` - 开发环境

**手动部署**：在 GitHub Actions 页面可以手动触发部署。

### 传统部署

参考上面的 Docker 部署步骤，或查看[完整部署文档](.github/DEPLOYMENT.md)。

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出建议！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

**开发流程**：
- 推送到 `main` 分支：自动部署到生产环境
- 推送到 `dev/v1.0.0` 分支：自动部署到开发环境
- 推送到其他 `dev/*` 分支：仅执行 CI 检查，不部署
- 创建 Pull Request：自动运行测试

**本地测试工具**：
```bash
# 测试 Docker 构建
./.github/scripts/test-build.sh

# 测试代码质量
./.github/scripts/local-lint.sh
```

## 📄 开源协议

本项目采用 MIT 协议，详见 [LICENSE](LICENSE) 文件。

## 📮 联系方式

如有问题或建议，欢迎通过以下方式联系：

- Issue: [GitHub Issues](https://github.com/yourusername/VeWealth/issues)
- Email: your.email@example.com

## 🙏 致谢

- [AKShare](https://github.com/akfamily/akshare) - 提供免费的股票数据接口
- [FastAPI](https://fastapi.tiangolo.com/) - 优秀的 Python Web 框架
- [Next.js](https://nextjs.org/) - 强大的 React 框架
- [Recharts](https://recharts.org/) - 灵活的图表库

---

⭐ 如果这个项目对您有帮助，请给个 Star！
