# Backend - A股股票平台API

基于 FastAPI 的股票数据API服务，采用分层架构设计。

## 📁 项目结构

```
backend/
├── main.py                 # 应用入口（生产模式）
├── dev_server.py          # 开发服务器（热重载） ⭐
├── requirements.txt        # 依赖包
├── README.md              # 本文档
├── logs/                  # 日志文件目录
│   └── vewealth.log       # 应用日志
└── app/                   # 主应用包
    ├── core/              # 核心配置
    │   ├── config.py      # 应用配置
    │   └── logger.py      # 日志配置
    ├── schemas/           # 数据模型
    │   └── stock.py       # 股票相关模型
    ├── services/          # 业务逻辑层
    │   └── stock_service.py  # 股票服务
    ├── utils/             # 工具函数
    │   └── data_processor.py # 数据处理
    └── routers/           # API路由
        └── stock.py       # 股票路由
```

## 🏗️ 架构设计

采用经典的三层架构模式：

- **Routers（路由层）**: 处理HTTP请求和响应
- **Services（服务层）**: 实现业务逻辑
- **Utils（工具层）**: 通用数据处理

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行服务

#### 开发模式（推荐）- 支持热重载

```bash
python dev_server.py
```

**特性：**
- ✅ 文件修改后自动重启服务器
- ✅ 实时应用代码更改，无需手动重启
- ✅ 提高开发效率
- ✅ 监控 `app/` 目录下的所有Python文件

**使用场景：**
- 本地开发和调试
- 快速迭代和测试
- API开发和测试

#### 生产模式

```bash
python main.py
```

服务将运行在 http://localhost:8001

### 运行测试

```bash
python test_api.py
```

## 📚 API文档

启动服务后访问：
- **Swagger UI**: http://localhost:8001/docs
- **ReDoc**: http://localhost:8001/redoc

## 📝 日志系统

项目采用规范的日志系统，所有日志统一管理：

### 日志文件位置
```
backend/logs/vewealth.log
```

### 查看日志
```bash
# 实时查看日志
tail -f logs/vewealth.log

# 查看错误日志
grep ERROR logs/vewealth.log

# 查看最近的日志
tail -n 100 logs/vewealth.log
```

### 测试日志系统
```bash
python test_logging.py
```

### 日志特性
- ✅ 统一的日志格式（包含时间、级别、模块、函数、行号）
- ✅ 异常自动记录traceback信息，方便debug
- ✅ 自动日志文件轮转（单文件最大10MB，保留5个备份）
- ✅ 控制台和文件双输出

## 🔌 主要接口

### 1. 搜索股票

```http
GET /api/stock/search?keyword=平安银行
```

**响应示例**:
```json
{
  "success": true,
  "results": [
    {
      "code": "000001",
      "name": "平安银行",
      "current_price": 12.34
    }
  ]
}
```

### 2. 获取股票数据

```http
GET /api/stock/data?symbol=000001&start_date=2024-01-01&end_date=2024-01-31&period=daily
```

**参数说明**:
- `symbol`: 股票代码
- `start_date`: 开始日期（YYYY-MM-DD）
- `end_date`: 结束日期（YYYY-MM-DD）
- `period`: 数据周期（1min/3min/5min/15min/30min/60min/daily）

**响应示例**:
```json
{
  "success": true,
  "symbol": "000001",
  "period": "daily",
  "chart_data": [...],
  "count": 20
}
```

## 🛠️ 技术栈

- **FastAPI**: 高性能Web框架
- **Pydantic**: 数据验证和设置管理
- **AKShare**: A股数据源
- **Pandas**: 数据处理
- **Uvicorn**: ASGI服务器

## 📦 依赖说明

| 包名 | 版本 | 用途 |
|------|------|------|
| fastapi | 0.118.2 | Web框架 |
| uvicorn | 0.37.0 | ASGI服务器 |
| akshare | 1.17.63 | 股票数据源 |
| pandas | 2.3.3 | 数据处理 |
| pydantic-settings | 2.0.3 | 配置管理 |

## ⚙️ 配置说明

配置文件位于 `app/core/config.py`，支持以下配置：

```python
APP_NAME = "VeWealth A股股票平台API"
APP_VERSION = "1.2.0"
HOST = "0.0.0.0"
PORT = 8001
CORS_ORIGINS = ["http://localhost:3000"]
MAX_SEARCH_RESULTS = 20
MAX_DAILY_QUERY_DAYS = 365
MAX_MINUTE_QUERY_DAYS = 10
```

## 🧪 开发指南

### 添加新功能

1. **定义数据模型** (`app/schemas/`)
2. **实现业务逻辑** (`app/services/`)
3. **添加API路由** (`app/routers/`)
4. **编写测试** (`tests/`)

### 代码规范

- 遵循PEP 8规范
- 使用类型注解
- 编写文档字符串
- 单元测试覆盖

### 测试

```bash
# 运行API测试
python test_api.py

# 运行单元测试（如果有）
pytest tests/
```

## 📊 数据来源

使用 [AKShare](https://github.com/akfamily/akshare) 库获取A股数据：

- 日线数据: `stock_zh_a_hist()`
- 分钟数据: `stock_zh_a_hist_min_em()`
- 实时行情: `stock_zh_a_spot_em()`

## 🩺 数据源健康检查与降级观测

多数据源（mootdx / Tushare / AKShare / 腾讯 / 东财）默认按 try/except 静默降级。
后端内置了源级探针 + 健康检查 / 监控 + 降级事件日志 / 告警，使数据源异常不再静默。

### 观测入口（`/api/health`）

| 接口 | 说明 |
| --- | --- |
| `GET /api/health` | 总体健康状态（ok / degraded / unhealthy / unknown） |
| `GET /api/health/sources?refresh=true` | 各数据源探针与运行状态快照（`refresh=true` 触发一轮实时探针） |
| `GET /api/health/metrics` | 按数据源聚合的监控指标（请求量 / 成功率 / 平均耗时） |
| `GET /api/health/events` | 最近的降级事件（失败 / 回退 / 恢复） |

### 工作原理

- **源级探针**：`app/providers/probes.py` 对每个数据源做轻量只读可达性检查
  （东财 / 腾讯拉取 3 根样本日 K，mootdx TCP 取 3 根，Tushare / AKShare 走最小查询）；
  未启用 / 依赖缺失的数据源标记为 `skipped`，不计入故障。
- **健康状态**：`app/core/source_health.py` 维护每个数据源的 up / down / skipped
  状态、连续失败次数、成功率与耗时 EMA；连续失败达到阈值自动升级为 ERROR 日志告警。
- **降级事件**：失败 / 回退 / 恢复事件写入有界环形缓冲，并输出结构化日志
  （`[data-source] event_type source=... detail=...`），供日志采集 / 告警系统消费。
- **定时检查**：APScheduler 按 `SOURCE_HEALTH_PROBE_CRON`（默认每 5 分钟）执行一轮探针。

### 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `SOURCE_HEALTH_PROBE_CRON` | `*/5 * * * *` | 探针运行周期 |
| `SOURCE_HEALTH_EVENT_LIMIT` | `200` | 降级事件环形缓冲上限 |
| `SOURCE_HEALTH_FAIL_THRESHOLD` | `3` | 连续失败升级为 ERROR 告警的阈值 |
| `SOURCE_HEALTH_PROBE_SYMBOL` | `000001` | 探针使用的样本股票代码 |

## 🔒 安全性

- CORS配置限制跨域访问
- 参数验证防止注入攻击
- 错误信息不暴露敏感数据

## 📈 性能优化

- 异步处理提高并发性能
- 数据验证使用Pydantic加速
- 建议添加缓存层（Redis）

## 🐛 故障排查

### 常见问题

1. **端口被占用**: 修改 `config.py` 中的 `PORT`
2. **AKShare超时**: 检查网络连接，可能数据源暂时不可用
3. **分钟数据获取失败**: 确认查询的是最近几天的数据

### 日志查看

```bash
# 启动时会显示详细日志
python main.py
```

## 📝 更新日志

### v1.2.0
- ✨ 新增全市场智能选股器（策略驱动扫描、板块/ST 过滤）
- ✨ 新增 GMM 成交量密度回测策略与交易信号
- ✨ 新增用户设置页（微信 OpenID 绑定、预警阈值）
- ✨ 数据源健康度检查与降级观测
- 🎨 UI 全面改版
- 🔧 CI 接入后端单元测试与前端核心逻辑测试

### v1.1.0
- ✨ 重构代码为分层架构
- ✨ 添加配置管理
- ✨ 完善错误处理
- 📚 添加架构文档

### v1.0.0
- 🎉 初始版本
- ✨ 股票搜索功能
- ✨ 多周期数据查询
- ✨ 3分钟合单功能

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

