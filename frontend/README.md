# Frontend - A股股票平台前端

基于 Next.js 14 的股票数据可视化前端应用。

## 安装

```bash
npm install
# 或
yarn install
```

## 开发

```bash
npm run dev
# 或
yarn dev
```

应用将运行在 http://localhost:3000

## 构建

```bash
npm run build
npm start
# 或
yarn build
yarn start
```

## 技术栈

- **Next.js 14** - React 框架（App Router）
- **TypeScript** - 类型安全
- **Tailwind CSS** - 样式框架
- **Recharts** - 图表库
- **Axios** - HTTP 客户端
- **date-fns** - 日期处理

## 项目结构

```
frontend/
├── app/
│   ├── page.tsx          # 主页面
│   ├── layout.tsx        # 根布局
│   ├── globals.css       # 全局样式
│   └── components/
│       └── StockChart.tsx  # 图表组件
├── public/               # 静态资源
├── package.json          # 依赖配置
├── tsconfig.json         # TypeScript配置
├── next.config.js        # Next.js配置
└── tailwind.config.js    # Tailwind配置
```

## 环境变量

创建 `.env.local` 文件：

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

