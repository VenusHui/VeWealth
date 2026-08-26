# VeWealth - A股股票智能分析与监控平台 💰

[![CI/CD Pipeline](https://github.com/VenusHui/VeWealth/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/VenusHui/VeWealth/actions/workflows/ci-cd.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Node.js 20](https://img.shields.io/badge/node-20-green.svg)](https://nodejs.org/)

一个基于 FastAPI 和 Next.js 的现代化A股股票数据分析平台，提供实时数据查询、Volume Profile 深度分析、策略回测、智能预警和微信通知等功能。

## ✨ 核心功能

### 📊 深度分析 (Depth / Volume Profile)
- **Volume Profile** - 按价格聚合历史成交量，直观展示筹码分布
- **GMM 多峰拟合** - 使用高斯混合模型（GMM）拟合成交量分布曲线，自动识别筹码密集区
- **客户端实时拟合** - 浏览器端 GMM 计算，即时响应用户交互
- **GMM 交易信号** - 基于成交量密度分布的多空交易信号

### 🎯 智能选股 (Screener)
- **全市场扫描** - 基于回测策略信号在全 A 股范围内筛选股票
- **策略驱动** - 复用内置策略模板（GMM 成交量密度、均线交叉等）
- **板块与 ST 过滤** - 按主板/创业板/科创板/北交所过滤，支持排除 ST
- **异步任务** - 扫描任务异步执行，支持实时进度与部分结果查询

### 🧪 策略回测 (Backtest)
- **内置策略模板** - 均线交叉、缩量下跌、GMM 成交量密度等多套策略
- **全市场扫描** - `strategy_select` 模式支持全 A 股扫描选股
- **股票池筛选** - 支持按板块（主板/创业板/科创板/北交所）、ST 状态过滤
- **异步任务** - 回测任务异步执行，支持进度查询与取消
- **三级钻取详情** - 概览 → 成交明细 → 回合交易 → 持仓快照 → 策略配置
- **CSV 导出** - 成交明细与回合交易支持导出
- **策略管理** - 策略可用性校验、Policy 五件套架构（排名/选股/配仓/风控/执行）

### 👁️ 监控列表
- **股票监控管理** - 添加关注的股票到个人监控列表
- **自动数据采集** - 定时任务每日自动采集监控股票的分时数据
- **长期数据存储** - 突破 AKShare 5日限制，PostgreSQL 永久保存历史数据
- **个性化设置** - 每只股票可单独设置预警开关和阈值

### ⚡ 智能预警
- **GMM 双向交易信号** - 基于成交量密度分布自动生成买卖预警
- **微信通知** - 通过微信公众号实时推送预警消息
- **预警历史** - 完整的预警历史记录，支持按方向（买/卖）筛选
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
- **mootdx** - TDX 数据接口（备用行情源）
- **scikit-learn** - GMM 模型和机器学习
- **wechatpy** - 微信公众号 SDK
- **JWT** - Token 认证

### 前端
- **Next.js 14** - React 框架（App Router）
- **TypeScript** - 类型安全
- **Ant Design 6** - 企业级 UI 组件库
- **Tailwind CSS** - 页面布局与辅助样式
- **Recharts** - 数据可视化图表库
- **Axios** - HTTP 客户端

## 📦 项目结构

```
VeWealth/
├── backend/                          # 后端 FastAPI 项目
│   ├── main.py                      # 应用主入口
│   ├── dev_server.py                # 热重载开发服务器
│   ├── app/
│   │   ├── core/                    # 核心配置
│   │   │   ├── config.py           # 应用配置
│   │   │   ├── database.py         # 数据库连接
│   │   │   ├── security.py         # 安全认证
│   │   │   ├── deps.py             # 依赖注入
│   │   │   ├── logger.py           # 日志配置
│   │   │   └── source_health.py    # 数据源健康度检查
│   │   ├── models/                 # 数据库模型
│   │   │   ├── user.py             # 用户模型
│   │   │   ├── watchlist.py        # 监控列表模型
│   │   │   ├── stock_data.py       # 股票数据模型
│   │   │   ├── backtest.py         # 回测记录模型
│   │   │   ├── backtest_job.py     # 回测任务模型
│   │   │   ├── alert_history.py    # 预警历史模型
│   │   │   └── security_universe.py # 股票池模型
│   │   ├── schemas/                # Pydantic 模型
│   │   │   ├── auth.py             # 认证相关
│   │   │   ├── watchlist.py        # 监控列表相关
│   │   │   ├── stock.py            # 股票相关
│   │   │   ├── backtest.py         # 回测相关
│   │   │   ├── alert.py            # 预警相关
│   │   │   └── screener.py         # 选股相关
│   │   ├── routers/                # API 路由
│   │   │   ├── auth.py             # 认证路由
│   │   │   ├── watchlist.py        # 监控列表路由
│   │   │   ├── stock.py            # 股票数据路由
│   │   │   ├── backtest.py         # 回测路由
│   │   │   ├── alert.py            # 预警历史路由
│   │   │   ├── scheduler.py        # 调度器管理路由
│   │   │   ├── health.py           # 健康检查路由
│   │   │   └── screener.py         # 智能选股路由
│   │   ├── services/               # 业务逻辑
│   │   │   ├── stock_service.py    # 股票服务
│   │   │   ├── data_collector.py   # 数据采集
│   │   │   ├── alert_service.py    # 预警服务
│   │   │   ├── wechat_service.py   # 微信服务
│   │   │   ├── scheduler.py        # 定时任务
│   │   │   ├── screener_service.py # 智能选股服务
│   │   │   └── backtest/           # 回测子系统
│   │   │       ├── engine.py       # 核心执行引擎
│   │   │       ├── service.py      # 编排层
│   │   │       ├── job_manager.py  # 任务生命周期
│   │   │       ├── registry.py     # 策略注册表
│   │   │       ├── costs.py        # 交易成本建模
│   │   │       ├── metrics.py      # 绩效指标
│   │   │       ├── strategy_management_service.py
│   │   │       ├── strategies/     # 策略实现
│   │   │       ├── policies/       # Policy 定义
│   │   │       └── validators/     # 策略校验
│   │   ├── providers/              # 行情数据源
│   │   │   ├── base.py             # 抽象接口
│   │   │   ├── akshare_provider.py # AKShare 实现
│   │   │   ├── astock_provider.py  # ASTock/TDX 实现
│   │   │   ├── astock_data.py      # ASTock 数据类型
│   │   │   └── probes.py           # 数据源探测
│   │   └── utils/                  # 工具函数
│   │       ├── data_processor.py   # GMM 数据处理
│   │       └── stock_data_fetcher.py
│   ├── scripts/                    # 维护脚本
│   │   └── refresh_a_share_symbols.py
│   ├── tests/                      # 单元测试
│   ├── data/                       # 静态数据
│   │   └── a_share_symbols.txt
│   ├── migration/                  # 数据库迁移
│   │   └── db/v1/                  # v1 迁移脚本
│   ├── setup_database.py           # 数据库初始化脚本
│   └── requirements.txt
│
├── frontend/                        # 前端 Next.js 项目
│   ├── app/
│   │   ├── page.tsx                # 首页（仪表盘）
│   │   ├── layout.tsx              # 根布局
│   │   ├── login/page.tsx          # 登录/注册
│   │   ├── depth/page.tsx          # 深度分析（Volume Profile + GMM）
│   │   ├── screener/page.tsx       # 智能选股（全市场扫描）
│   │   ├── watchlist/page.tsx      # 监控列表
│   │   ├── backtest/               # 策略回测
│   │   │   ├── page.tsx            # 回测中心
│   │   │   └── strategies/[strategyId]/page.tsx  # 策略详情
│   │   ├── alerts/page.tsx         # 预警历史
│   │   ├── settings/page.tsx       # 用户设置
│   │   ├── components/             # 共享组件
│   │   │   ├── Navbar.tsx          # 导航栏
│   │   │   ├── ui-shell.tsx        # 页面布局壳
│   │   │   ├── DepthChart.tsx      # Volume Profile 图表
│   │   │   ├── DepthStatistics.tsx # 深度统计
│   │   │   └── DepthToolbar.tsx    # 深度工具栏
│   │   └── lib/                    # 工具库
│   │       ├── api.ts              # API 地址解析
│   │       ├── auth.ts             # 认证工具
│   │       ├── depthChartUtils.ts  # 图表计算
│   │       └── marketColors.ts     # A股配色
│   ├── package.json
│   └── tailwind.config.js
│
├── docs/                            # 设计文档
│   ├── plans/                       # 架构设计计划
│   └── frontend/                    # 前端迁移计划
├── README.md
└── LICENSE
```

## 🚀 快速开始

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
ENV=local docker-compose up -d

# 启动生产环境
./docker-start.sh

# 或手动指定环境
ENV=prod docker-compose up -d --build
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

- Python 3.12+
- Node.js 20+
- PostgreSQL 12+

#### 1. 克隆项目

```bash
git clone https://github.com/yourusername/VeWealth.git
cd VeWealth
```

#### 2. 一键启动

```bash
./start.sh    # 同时启动后端 (dev_server.py) + 前端 (npm run dev)
```

#### 3. 数据库设置（手动）

如果需要手动设置数据库：

创建 PostgreSQL 数据库：

```sql
CREATE DATABASE vewealth;
CREATE USER vewealth WITH PASSWORD 'vewealth123';
GRANT ALL PRIVILEGES ON DATABASE vewealth TO vewealth;
```

#### 4. 后端设置

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动后端服务（默认使用 local 环境）
python dev_server.py

# 或指定环境
ENV=local python main.py   # 本地环境
ENV=prod python main.py    # 生产环境
```

后端将在 http://localhost:8001 运行

#### 5. 前端设置

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

### 2. 首页仪表盘

登录后首页展示三大指数实时行情、监控列表预览和最新预警记录。

### 3. 深度分析（Volume Profile）

1. 点击导航栏的"深度数据"
2. 输入股票代码（如：000001）查看 Volume Profile
3. 图表展示价格-成交量分布及 GMM 多峰拟合曲线
4. 查看基于成交量密度的多空交易信号
5. 可调节参数控制 GMM 拟合行为

### 4. 智能选股（全市场扫描）

1. 点击导航栏的"选股"
2. 选择策略（GMM 成交量密度、均线交叉等）并配置参数
3. 按板块（主板/创业板/科创板/北交所）过滤，可选择排除 ST
4. 启动全市场扫描，实时查看扫描进度与命中股票
5. 查看命中结果对应的策略信号说明

### 5. 监控列表

1. 点击导航栏的"监控台"
2. 点击"+ 添加股票"添加关注的股票
3. 设置是否启用预警和预警阈值
4. 系统会在每个交易日 15:00 自动采集分时数据

### 6. 策略回测

1. 点击导航栏的"回测中心"
2. 选择策略模板（均线交叉、缩量下跌、GMM 成交量密度等）
3. 配置参数、选择股票池与回测区间
4. 提交回测任务，查看实时进度
5. 回测完成后查看概览、成交明细、回合交易、持仓快照、策略配置

### 7. 价格预警

1. 在监控列表中启用股票的预警功能
2. 在用户设置中绑定微信 OpenID（需要关注公众号）
3. 系统会在交易时间每 5 分钟检查一次
4. 当触发 GMM 交易信号或价格超过阈值时，通过微信公众号发送通知
5. 在"预警历史"页面查看所有历史预警记录

## 🎯 核心算法

### Volume Profile 与 GMM 拟合

系统将指定时间窗口内的分时数据按价格聚合为 Volume Profile，并使用高斯混合模型（Gaussian Mixture Model）对成交量分布进行多峰拟合：

1. **数据准备**：将分时/日线数据按价格聚合，得到每个价格点的总成交量
2. **模型选择**：通过 BIC（贝叶斯信息准则）自动选择最优的峰数（2-5个峰）
3. **拟合计算**：使用 EM 算法拟合多峰正态分布
4. **曲线生成**：在价格范围内生成拟合曲线点，叠加在 Volume Profile 柱状图上
5. **交易信号**：基于当前价格在 GMM 分布中的密度位置，生成多空方向信号

支持客户端（浏览器端）实时 GMM 拟合，即时响应用户参数调整。

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

### 回测记录表 (backtest_runs)
- user_id, name, status
- strategy_id, strategy_params, symbols
- start_date, end_date, initial_cash, benchmark
- cost_config, summary, equity_curve, trades, warnings
- created_at / updated_at

### 回测任务表 (backtest_jobs)
- job_id, user_id, status, progress
- request_params, result_run_id
- created_at / updated_at

### 预警历史表 (alert_history)
- user_id, stock_code, stock_name
- alert_direction (buy/sell), current_price
- created_at

### 股票池表 (security_universe)
- stock_code, stock_name
- market (SH/SZ/BJ), board (main/gem/star/bse)
- is_st, is_active
- list_date, delist_date

## 🔧 API 接口

### 认证相关
- `POST /api/auth/register` - 用户注册
- `POST /api/auth/login` - 用户登录
- `GET /api/auth/me` - 获取当前用户信息
- `PUT /api/auth/me` - 更新用户信息

### 监控列表
- `GET /api/watchlist` - 获取监控列表
- `POST /api/watchlist` - 添加股票
- `PUT /api/watchlist/{id}` - 更新监控项
- `DELETE /api/watchlist/{id}` - 删除监控项

### 股票数据
- `GET /api/stock/search` - 搜索股票
- `GET /api/stock/kline` - 获取 K 线数据
- `GET /api/stock/volume-profile` - 获取 Volume Profile 数据
- `GET /api/stock/depth` - 获取深度分析数据
- `GET /api/stock/info` - 获取股票基本信息
- `GET /api/stock/quotes` - 批量获取实时行情

### 策略回测
- `GET /api/backtest/strategies` - 获取可用策略列表
- `POST /api/backtest/run` - 同步执行回测
- `GET /api/backtest/runs` - 回测记录列表
- `GET /api/backtest/runs/{id}` - 回测记录详情
- `GET /api/backtest/runs/{id}/trades` - 成交明细
- `GET /api/backtest/runs/{id}/rounds` - 回合交易
- `GET /api/backtest/runs/{id}/snapshots` - 持仓快照
- `GET /api/backtest/runs/{id}/strategy-config` - 策略配置
- `POST /api/backtest/jobs` - 创建异步回测任务
- `GET /api/backtest/jobs` - 查询任务列表
- `GET /api/backtest/jobs/{id}` - 查询任务详情
- `POST /api/backtest/jobs/{id}/cancel` - 取消回测任务
- `POST /api/backtest/jobs/{id}/retry` - 重试回测任务
- `GET /api/backtest/strategy-management/list` - 策略管理列表
- `GET /api/backtest/universe/stats` - 股票池统计
- `GET /api/backtest/runs/{id}/facts` - 回测绩效事实
- `GET /api/backtest/runs/{id}/trades/export` - 导出成交明细 CSV
- `GET /api/backtest/runs/{id}/rounds/export` - 导出回合交易 CSV

### 智能选股
- `POST /api/screener/scan` - 启动全市场策略选股扫描
- `GET /api/screener/scans` - 扫描历史列表
- `GET /api/screener/scans/{scan_id}` - 查询扫描状态与结果

### 预警历史
- `GET /api/alerts` - 获取预警历史记录（支持按方向筛选）

### 调度器管理
- `POST /api/scheduler/run/collect-data` - 手动触发数据采集
- `POST /api/scheduler/run/check-alerts` - 手动触发预警检查

### 健康检查
- `GET /api/health` - 服务健康检查
- `GET /api/health/sources` - 数据源健康度
- `GET /api/health/metrics` - 数据源指标
- `GET /api/health/events` - 数据源降级事件

详细 API 文档：http://localhost:8001/docs

## 🎨 界面预览

### 首页仪表盘
- 三大指数实时行情
- 监控列表预览
- 最新预警记录

### 深度分析页面
- Volume Profile 交互式图表
- GMM 多峰拟合曲线叠加
- 双向交易信号展示

### 智能选股页面
- 策略选择与参数配置
- 板块 / ST 过滤
- 全市场扫描进度与命中结果

### 策略回测页面
- 策略选择与参数配置
- 回测记录列表与状态跟踪
- 三级钻取详情（概览/成交/回合/快照/配置）

### 监控列表页面
- 添加/删除股票
- 预警设置
- 实时状态管理

### 用户设置页面
- 微信 OpenID 绑定
- 默认预警阈值设置

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
- 前端通过 `getApiBaseUrl()` 在运行时解析后端地址（`window.location.hostname:8001`）
- 检查 CORS 配置是否包含当前访问域名

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

本项目已配置完整的 CI/CD 流程，推送代码到 `main` 或 `dev/**` 分支自动触发构建和部署。

**工作流程**：
1. 推送代码到 `main` 或 `dev/**` 分支
2. 自动执行代码检查（Black / ESLint）和后端单元测试（pytest）
3. 自动执行 Docker 构建
4. 通过 SSH 自动部署到服务器
5. 执行健康检查确保服务正常

**手动部署**：在 GitHub Actions 页面可以手动触发部署（`manual-deploy.yml`）。

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出建议！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

**开发流程**：
- 推送到 `main` 分支：自动部署到生产环境
- 推送到 `dev/*` 分支：自动执行 CI 检查
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
