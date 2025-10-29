# VeWealth 部署指南

本文档详细说明如何在生产环境部署 VeWealth 平台。

## 系统要求

- Ubuntu 20.04+ / CentOS 8+ / macOS
- Python 3.8+
- Node.js 18+
- PostgreSQL 12+
- Nginx（可选，用于反向代理）
- 微信公众号（可选，用于消息通知）

## 1. 安装 PostgreSQL

### Ubuntu/Debian

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### macOS

```bash
brew install postgresql@14
brew services start postgresql@14
```

### 配置数据库

```bash
# 切换到 postgres 用户
sudo -u postgres psql

# 创建数据库和用户
CREATE DATABASE vewealth;
CREATE USER vewealth WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE vewealth TO vewealth;

# 退出
\q
```

## 2. 部署后端

### 2.1 创建项目目录

```bash
cd /opt
sudo mkdir vewealth
sudo chown $USER:$USER vewealth
cd vewealth
```

### 2.2 克隆代码

```bash
git clone https://github.com/yourusername/VeWealth.git .
```

### 2.3 安装 Python 依赖

```bash
cd backend

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.4 配置环境变量

创建 `.env` 文件：

```bash
nano .env
```

添加以下内容（根据实际情况修改）：

```bash
# 数据库配置
DATABASE_URL=postgresql://vewealth:your_secure_password@localhost:5432/vewealth

# JWT 配置
SECRET_KEY=your-production-secret-key-change-this-to-random-string
MASTER_KEY=your-master-key-for-registration
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200

# 微信公众号配置（如果使用微信通知）
WECHAT_APP_ID=your_wechat_app_id
WECHAT_APP_SECRET=your_wechat_app_secret
WECHAT_TOKEN=your_wechat_token
WECHAT_ENCODING_AES_KEY=your_encoding_aes_key

# 定时任务配置
SCHEDULER_ENABLED=true
DATA_COLLECT_CRON=0 15 * * 1-5
ALERT_CHECK_CRON=*/5 9-15 * * 1-5

# 预警配置
DEFAULT_ALERT_THRESHOLD=0.7
```

### 2.5 初始化数据库

```bash
# 激活虚拟环境
source venv/bin/activate

# 运行一次后端应用以创建数据表
python main.py
# Ctrl+C 停止

# 或者使用 Python 脚本初始化
python -c "from app.core.database import init_db; init_db()"
```

### 2.6 使用 Supervisor 管理后端进程

安装 Supervisor：

```bash
sudo apt install supervisor  # Ubuntu/Debian
# 或
sudo yum install supervisor  # CentOS
```

创建 Supervisor 配置：

```bash
sudo nano /etc/supervisor/conf.d/vewealth-backend.conf
```

添加以下内容：

```ini
[program:vewealth-backend]
directory=/opt/vewealth/backend
command=/opt/vewealth/backend/venv/bin/python main.py
user=yourusername
autostart=true
autorestart=true
stderr_logfile=/var/log/vewealth/backend.err.log
stdout_logfile=/var/log/vewealth/backend.out.log
environment=PATH="/opt/vewealth/backend/venv/bin"
```

创建日志目录并启动：

```bash
sudo mkdir -p /var/log/vewealth
sudo chown $USER:$USER /var/log/vewealth
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start vewealth-backend
```

查看状态：

```bash
sudo supervisorctl status vewealth-backend
```

## 3. 部署前端

### 3.1 安装 Node.js 和 npm

```bash
# 使用 nvm 安装 Node.js
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
source ~/.bashrc
nvm install 18
nvm use 18
```

### 3.2 构建前端

```bash
cd /opt/vewealth/frontend

# 安装依赖
npm install

# 创建环境变量文件
nano .env.local
```

添加：

```bash
NEXT_PUBLIC_API_URL=http://your-domain.com/api
# 或者如果使用 IP
NEXT_PUBLIC_API_URL=http://your-server-ip:8001
```

构建生产版本：

```bash
npm run build
```

### 3.3 使用 PM2 管理前端进程

安装 PM2：

```bash
npm install -g pm2
```

启动前端：

```bash
cd /opt/vewealth/frontend
pm2 start npm --name "vewealth-frontend" -- start
pm2 save
pm2 startup
```

查看状态：

```bash
pm2 status
pm2 logs vewealth-frontend
```

## 4. 配置 Nginx 反向代理（推荐）

### 4.1 安装 Nginx

```bash
sudo apt install nginx  # Ubuntu/Debian
# 或
sudo yum install nginx  # CentOS
```

### 4.2 配置 Nginx

创建配置文件：

```bash
sudo nano /etc/nginx/sites-available/vewealth
```

添加以下内容：

```nginx
server {
    listen 80;
    server_name your-domain.com;  # 替换为你的域名

    # 前端
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # 后端 API
    location /api {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API 文档
    location /docs {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location /redoc {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/vewealth /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 4.3 配置 HTTPS（推荐）

使用 Let's Encrypt 免费 SSL 证书：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

按照提示完成配置，Certbot 会自动配置 HTTPS。

## 5. 配置微信公众号（可选）

### 5.1 申请微信公众号

1. 访问 [微信公众平台](https://mp.weixin.qq.com/)
2. 注册服务号或订阅号
3. 完成认证（服务号需要）

### 5.2 获取配置信息

在公众号后台获取：
- AppID
- AppSecret
- 配置服务器 URL 和 Token

### 5.3 配置模板消息

1. 在公众号后台申请模板消息权限
2. 添加价格预警模板
3. 记录模板 ID，更新到代码中

### 5.4 获取用户 OpenID

用户关注公众号后，通过微信网页授权获取 OpenID，并在系统中绑定。

## 6. 防火墙配置

如果使用云服务器，需要在安全组中开放端口：

- 80（HTTP）
- 443（HTTPS）
- 8001（后端 API，可选，建议通过 Nginx 代理）
- 3000（前端，可选，建议通过 Nginx 代理）

如果使用 ufw：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 7. 监控和日志

### 查看后端日志

```bash
# Supervisor 日志
sudo tail -f /var/log/vewealth/backend.out.log
sudo tail -f /var/log/vewealth/backend.err.log

# 或直接查看应用输出
sudo supervisorctl tail -f vewealth-backend stdout
```

### 查看前端日志

```bash
pm2 logs vewealth-frontend
```

### 查看 Nginx 日志

```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## 8. 备份策略

### 8.1 数据库备份

创建备份脚本：

```bash
sudo nano /opt/vewealth/backup.sh
```

添加：

```bash
#!/bin/bash
BACKUP_DIR="/opt/vewealth/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# 备份数据库
PGPASSWORD=your_secure_password pg_dump -h localhost -U vewealth vewealth > $BACKUP_DIR/vewealth_$DATE.sql

# 保留最近7天的备份
find $BACKUP_DIR -name "vewealth_*.sql" -mtime +7 -delete

echo "Backup completed: vewealth_$DATE.sql"
```

设置执行权限并添加到 crontab：

```bash
chmod +x /opt/vewealth/backup.sh

# 每天凌晨2点备份
crontab -e
# 添加：
0 2 * * * /opt/vewealth/backup.sh >> /var/log/vewealth/backup.log 2>&1
```

### 8.2 恢复数据库

```bash
PGPASSWORD=your_secure_password psql -h localhost -U vewealth vewealth < backup_file.sql
```

## 9. 更新部署

### 更新后端

```bash
cd /opt/vewealth
git pull origin main

cd backend
source venv/bin/activate
pip install -r requirements.txt

sudo supervisorctl restart vewealth-backend
```

### 更新前端

```bash
cd /opt/vewealth
git pull origin main

cd frontend
npm install
npm run build

pm2 restart vewealth-frontend
```

## 10. 故障排查

### 后端无法启动

```bash
# 检查日志
sudo supervisorctl tail -f vewealth-backend stderr

# 检查数据库连接
psql -h localhost -U vewealth -d vewealth

# 手动运行测试
cd /opt/vewealth/backend
source venv/bin/activate
python main.py
```

### 前端无法访问

```bash
# 检查 PM2 状态
pm2 status
pm2 logs vewealth-frontend

# 检查端口占用
netstat -tulpn | grep 3000
```

### 数据库连接失败

```bash
# 检查 PostgreSQL 状态
sudo systemctl status postgresql

# 检查连接字符串
cat /opt/vewealth/backend/.env | grep DATABASE_URL

# 测试连接
psql -h localhost -U vewealth -d vewealth
```

## 11. 性能优化

### PostgreSQL 优化

编辑 postgresql.conf：

```bash
sudo nano /etc/postgresql/14/main/postgresql.conf
```

调整参数（根据服务器配置）：

```
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 4MB
min_wal_size = 1GB
max_wal_size = 4GB
```

重启 PostgreSQL：

```bash
sudo systemctl restart postgresql
```

### 定期维护

添加到 crontab：

```bash
# 每周日凌晨3点执行 VACUUM
0 3 * * 0 psql -h localhost -U vewealth -d vewealth -c "VACUUM ANALYZE;" >> /var/log/vewealth/maintenance.log 2>&1
```

## 12. 安全建议

1. **定期更新系统和软件包**
2. **使用强密码**
3. **限制 SSH 访问**（禁用 root 登录，使用密钥认证）
4. **启用防火墙**
5. **定期备份数据**
6. **监控日志异常**
7. **使用 HTTPS**
8. **定期更新依赖包**

---

部署完成后，访问 `http://your-domain.com` 即可使用平台！

