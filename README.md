# VeWealth - A股股票分析平台

一个基于 FastAPI 和 Next.js 的现代化A股股票数据分析平台，实时获取股票数据并进行可视化分析。

## 技术栈

### 后端
- **FastAPI** - 现代化、高性能的Python Web框架
- **AKShare** - 免费开源的股票数据接口
- **Pandas** - 数据处理库

### 前端
- **Next.js 14** - React框架（App Router）
- **TypeScript** - 类型安全
- **Tailwind CSS** - 现代化UI样式
- **Recharts** - 数据可视化图表库
- **Axios** - HTTP客户端

## 功能特性

✅ **股票搜索** - 支持通过代码或名称搜索A股股票  
✅ **多周期数据** - 支持1分钟、3分钟、5分钟、15分钟、30分钟、60分钟、日线多种数据粒度  
✅ **时间范围选择** - 自定义查询的起止日期  
✅ **双折线图展示** - 价格走势图和成交量折线图分开展示，清晰直观  
✅ **3分钟合单** - 支持将1分钟数据重采样为3分钟合单数据  
✅ **实时数据** - 通过AKShare实时获取最新股票数据  
✅ **响应式设计** - 适配各种屏幕尺寸  

## 项目结构

```
VeWealth/
├── backend/              # 后端FastAPI项目（分层架构）
│   ├── main.py          # FastAPI主应用入口
│   ├── app/             # 主应用包
│   │   ├── routers/     # API路由层
│   │   ├── services/    # 业务逻辑层
│   │   ├── utils/       # 工具函数层
│   │   ├── schemas/     # 数据模型层
│   │   └── core/        # 核心配置
│   ├── requirements.txt # Python依赖
│   ├── ARCHITECTURE.md  # 架构说明
│   └── .gitignore
├── frontend/            # 前端Next.js项目
│   ├── app/            # Next.js App Router
│   │   ├── page.tsx    # 主页面
│   │   ├── layout.tsx  # 布局组件
│   │   ├── globals.css # 全局样式
│   │   └── components/ # React组件
│   │       └── StockChart.tsx  # 图表组件
│   ├── package.json    # Node.js依赖
│   ├── tsconfig.json   # TypeScript配置
│   ├── next.config.js  # Next.js配置
│   └── tailwind.config.js  # Tailwind配置
└── README.md           # 项目文档
```

## 快速开始

### 前置要求

- Python 3.8+
- Node.js 18+
- npm 或 yarn

### 后端安装与启动

1. 进入后端目录：
```bash
cd backend
```

2. 创建虚拟环境（推荐）：
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

3. 安装依赖：
```bash
pip install -r requirements.txt
```

4. 启动后端服务：
```bash
python main.py
```

后端服务将运行在 `http://localhost:8000`

### 前端安装与启动

1. 进入前端目录：
```bash
cd frontend
```

2. 安装依赖：
```bash
npm install
# 或
yarn install
```

3. 启动开发服务器：
```bash
npm run dev
# 或
yarn dev
```

前端应用将运行在 `http://localhost:3000`

## 使用说明

1. **选择数据粒度**
   - **1分钟（类似每笔）** - 获取最细粒度的分钟数据
   - **3分钟合单** - 将1分钟数据聚合为3分钟周期
   - **5分钟、15分钟、30分钟、60分钟** - 不同时间周期
   - **日线** - 每日收盘数据
   - ⚠️ 注意：分钟级数据通常只支持查询最近几天的数据

2. **搜索股票**
   - 在搜索框输入股票代码（如：000001）或名称（如：平安银行）
   - 点击"搜索"按钮或按回车键
   - 从下拉列表中选择目标股票

3. **设置日期范围**
   - 选择开始日期和结束日期
   - 日线默认为最近30天
   - 分钟线默认为最近1天

4. **查询数据**
   - 点击"查询股票数据"按钮
   - 等待数据加载

5. **查看图表**
   - 上方图表显示价格走势
   - 下方图表显示成交量变化
   - 鼠标悬停在数据点上查看详细信息（包括时间、价格、成交量、最高价、最低价）

## API接口说明

### 搜索股票
```
GET /api/stock/search?keyword={keyword}
```

参数：
- `keyword`: 股票代码或名称关键词

返回示例：
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

### 获取股票数据
```
GET /api/stock/data?symbol={symbol}&start_date={start_date}&end_date={end_date}&period={period}
```

参数：
- `symbol`: 股票代码（如：000001）
- `start_date`: 开始日期（格式：YYYY-MM-DD）
- `end_date`: 结束日期（格式：YYYY-MM-DD）
- `period`: 数据周期（可选值：1min、3min、5min、15min、30min、60min、daily，默认：daily）

返回示例：
```json
{
  "success": true,
  "symbol": "000001",
  "start_date": "2024-01-01",
  "end_date": "2024-01-31",
  "period": "3min",
  "data": [...],
  "chart_data": [
    {
      "datetime": "2024-01-01 09:30:00",
      "price": 12.34,
      "volume": 1234567,
      "open": 12.30,
      "high": 12.40,
      "low": 12.25
    }
  ],
  "count": 240
}
```

## 架构特点

### 后端架构

采用**分层架构**设计，代码组织清晰：

- **Routers（路由层）**: 处理HTTP请求和响应
- **Services（服务层）**: 实现业务逻辑
- **Utils（工具层）**: 通用数据处理函数
- **Schemas（模型层）**: Pydantic数据模型
- **Core（配置层）**: 应用配置管理

详见 [`backend/ARCHITECTURE.md`](backend/ARCHITECTURE.md)

### 前端架构

基于 **Next.js 14 App Router**：

- 服务端渲染（SSR）支持
- 组件化开发
- TypeScript类型安全
- Tailwind CSS样式管理

## 开发计划

- [x] 添加折线图展示价格和成交量
- [x] 支持多种数据粒度（1分钟、3分钟合单等）
- [x] 后端代码重构为分层架构
- [ ] 添加K线图（蜡烛图）
- [ ] 支持多股票对比
- [ ] 添加技术指标计算（MA、MACD、RSI等）
- [ ] 实现数据缓存机制（Redis）
- [ ] 添加用户收藏功能
- [ ] 支持导出数据为Excel/CSV
- [ ] 单元测试和集成测试

## 注意事项

1. AKShare数据来源于公开数据，仅供学习和研究使用
2. 股票数据存在延迟，不建议用于实盘交易决策
3. 请合理使用API，避免频繁请求导致被限流
4. 首次运行时AKShare可能需要下载数据，请耐心等待

## 常见问题

**Q: 为什么搜索不到某些股票？**  
A: 请确保输入的是A股股票代码或名称，暂不支持港股、美股等其他市场。

**Q: 数据获取失败怎么办？**  
A: 检查网络连接，确认股票代码正确，或稍后重试。AKShare依赖外部数据源，可能存在临时不可用的情况。

**Q: 如何修改API端口？**  
A: 修改 `backend/main.py` 中的端口配置，同时更新 `frontend/.env.local` 中的 `NEXT_PUBLIC_API_URL`。

## 许可证

本项目采用 MIT 许可证。

## 贡献

欢迎提交 Issue 和 Pull Request！

## 联系方式

如有问题或建议，请通过 Issue 与我们联系。

---

**免责声明**：本平台仅供学习和研究使用，不构成任何投资建议。投资有风险，入市需谨慎。

