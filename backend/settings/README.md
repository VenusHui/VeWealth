# backend/settings - 多环境配置

本目录存放后端的环境配置文件（**不入库**，仅存在于部署/开发环境）：

- `.local.env` - 本地开发环境配置（`ENV=local`）
- `.prod.env` - 生产环境配置（`ENV=prod`）

`.env` 文件已通过 `.gitignore`（`backend/settings/.*.env`）排除，请勿提交。

## 如何加载

`backend/app/core/config.py` 根据 `ENV` 环境变量加载对应文件：

| ENV 值    | 加载文件                 |
| --------- | ------------------------ |
| `local`（默认） | `backend/settings/.local.env` |
| `prod`    | `backend/settings/.prod.env` |

```bash
python dev_server.py        # 默认 local
ENV=local python main.py    # 本地环境
ENV=prod python main.py     # 生产环境
```

Docker 部署时通过 `docker-compose.yml` 将本目录挂载到容器 `backend/settings`（只读）。

## 关键配置项

| 配置项 | 说明 | 生产环境要求 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 连接串 | 必填 |
| `SECRET_KEY` | JWT 签名密钥 | 必改（`openssl rand -base64 32`） |
| `MASTER_KEY` | 注册主密钥 | 必改 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 微信公众号配置 | 启用微信通知时必填 |
| `CORS_ORIGINS` | 允许的前端来源（JSON 数组） | 必改（加入实际访问域名） |
| `DATA_PROVIDER` | 行情数据源（`astock` / `akshare`） | 默认 `astock` |
| `SCHEDULER_ENABLED` | 是否启用定时任务 | 默认 `true` |
| `TUSHARE_TOKEN` | Tushare 备用源 Token | 可选 |

完整字段见 `backend/app/core/config.py` 中的 `Settings` 类（Pydantic `extra="ignore"`，未在类中声明的键会被静默忽略）。
