# 多阶段构建：第一阶段 - 构建
FROM node:20-alpine AS builder

# 设置工作目录
WORKDIR /app

# 接收构建参数
ARG NEXT_PUBLIC_API_URL=http://localhost:8001

# 设置为环境变量，供 Next.js 构建时使用
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

# 复制 package.json 和 package-lock.json
COPY package*.json ./

# 安装依赖（npm ci 依赖 package-lock.json，安装更快速且可复现）
RUN npm ci

# 复制应用代码
COPY . .

# 确保 public 目录存在
RUN mkdir -p public

# 构建 Next.js 应用
RUN npm run build

# 多阶段构建：第二阶段 - 运行
FROM node:20-alpine AS runner

WORKDIR /app

# 设置环境变量
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# 创建非 root 用户
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# 复制必要的文件
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# 切换到非 root 用户
USER nextjs

# 暴露端口
EXPOSE 3000

# 设置环境变量
ENV PORT=3000 \
    HOSTNAME="0.0.0.0"

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000', (r) => {if(r.statusCode !== 200) throw new Error(r.statusCode)})" || exit 1

# 启动命令
CMD ["node", "server.js"]

