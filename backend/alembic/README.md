# Alembic 数据库迁移

本项目使用 [Alembic](https://alembic.sqlalchemy.org/) 管理 PostgreSQL schema 变更，
迁移脚本位于 `backend/alembic/versions/`，配置文件为 `backend/alembic.ini`。

## 背景

v1.2.0 之前 schema 变更靠零散脚本（`backend/migration/db/v1/`）+ `init_db()` 自动建表。
v1.2.0 发布后基线稳定，将当前完整 schema 固化为第一个迁移版本
`0001_initial_baseline`（对应历史零散脚本已全部并入该基线并删除），
此后所有 schema 变更一律通过新增 Alembic 迁移版本管理。

## 与 init_db() 的关系

- `init_db()`（`app/core/database.py`）在应用启动时按 ORM 模型 `create_all` 建表，
  作为全新部署的快速路径保留。
- `init_db()` 建表后会自动把数据库标记到 Alembic 基线
  （`stamp_alembic_head_if_unversioned`）：仅当数据库尚无 `alembic_version` 表时
  stamp head，避免干预已纳入 Alembic 管理的库、也避免跳过未应用的迁移。
- 两者描述的是同一份 schema：`alembic upgrade head` 与 `init_db()` 产出的表结构一致。
  新增/修改模型时，除了更新 ORM 模型，还必须生成对应的 Alembic 迁移，
  保证迁移链始终能重建当前 schema。

## 使用前提

1. 已安装依赖：`pip install -r requirements.txt`（含 `alembic==1.13.1`）。
2. 数据库连接配置与后端一致：
   - 默认读取 `backend/settings/.local.env`（`ENV=local`）或 `backend/settings/.prod.env`（`ENV=prod`）；
   - 或直接设置环境变量 `DATABASE_URL`（优先级最高）。
3. 所有命令在 `backend/` 目录下执行。

## 常用命令

### 1. 全新数据库建表

```bash
cd backend
alembic upgrade head
```

等价于启动时 `init_db()` 的效果，会创建全部表并写入 `alembic_version`。

### 2. 存量数据库接入（已有 v1.2.0 及之后 schema）

数据库已具备全部表/字段，不应再执行 `upgrade`（会因表已存在而报错），
只需把基线标记为已应用：

```bash
cd backend
alembic stamp head
```

> 判断标准：库内 `alembic_version` 表是否存在。不存在则执行 `stamp head`；
> 若通过 `init_db()` 启动过，该表通常已自动标记，无需手动操作。

### 3. 新增一次 schema 变更

1. 修改 `backend/app/models/` 下对应 ORM 模型。
2. 生成迁移草稿（对比当前 DB 与模型差异，自动写入新文件）：

```bash
cd backend
alembic revision --autogenerate -m "describe_change"
```

3. **人工审查**生成的 `alembic/versions/<rev>_describe_change.py`：
   确认 up/downgrade 语句符合预期（autogenerate 可能漏掉/多出索引、类型变化等），
   必要时手改。
4. 应用迁移：

```bash
alembic upgrade head
```

### 4. 查看迁移状态

```bash
alembic current          # 当前库所在版本
alembic history          # 迁移链历史
```

### 5. 回滚

```bash
alembic downgrade -1        # 回滚到上一个版本
alembic downgrade 0001_initial_baseline   # 回滚到指定版本
```

回滚到基线上一个版本即 `downgrade base`（删库，谨慎使用）：

```bash
alembic downgrade base
```

> 迁移会写入 `alembic_version` 表，因此回滚是版本化的、可追踪的，
> 与旧零散脚本的一次性 ALTER 不同。

## 目录结构

```
backend/
├── alembic.ini            # Alembic 配置（URL 由 env.py 用应用配置覆盖）
└── alembic/
    ├── env.py             # 绑定 app.core.database.Base.metadata，复用 settings.DATABASE_URL
    ├── script.py.mako     # 新迁移文件模板
    ├── README.md          # 本文档
    └── versions/          # 迁移版本
        └── 0001_initial_baseline.py   # 基线：v1.2.0 完整 schema 快照
```
