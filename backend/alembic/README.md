# Alembic 数据库迁移

本项目使用 [Alembic](https://alembic.sqlalchemy.org/) 管理 PostgreSQL schema 变更，
迁移脚本位于 `backend/alembic/versions/`，配置文件为 `backend/alembic.ini`。

## 背景

v1.2.0 之前 schema 变更靠零散脚本（`backend/migration/db/v1/`）+ `init_db()` 自动建表。
v1.2.0 发布后基线稳定，将当前完整 schema 固化为第一个迁移版本
`0001_initial_baseline`（对应历史零散脚本已全部并入该基线并删除），
此后所有 schema 变更一律通过新增 Alembic 迁移版本管理。

## 与 init_db() 的关系

`init_db()`（`app/core/database.py`）在应用启动（lifespan）时执行，与 Alembic 并存：

- **数据库已纳入 Alembic 管理**（存在 `alembic_version` 表）：执行
  `alembic upgrade head` 应用待执行迁移。这是 schema 变更在生产部署路径上生效的入口
  （`docker-compose up -d --build` → `python main.py` → lifespan `init_db()`），
  新增迁移无需额外手工步骤。
- **数据库未纳入 Alembic 管理**：按 ORM 模型 `create_all` 建表（全新部署的快速路径），
  随后 `stamp head` 把基线标记为已应用，避免后续 `upgrade` 重复建表。
- 两种路径产出的都是同一份 schema：`alembic upgrade head` 与 `init_db()` 的
  `create_all` 结果逐表/列/索引/约束一致（含列注释）。
- 多 worker 并发启动时，用 postgres advisory lock 串行化迁移/建表，避免竞争。

> ⚠️ 若已纳入 Alembic 管理的库存在待执行迁移，`init_db()` 会执行 `upgrade head`；
> 迁移失败会直接抛出并阻止应用启动（避免在缺列的 schema 上运行期报错）。

新增/修改模型时，除了更新 ORM 模型，还必须生成对应的 Alembic 迁移，
保证迁移链始终能重建当前 schema（`alembic check` 应无漂移）。

## 使用前提

1. 已安装依赖：`pip install -r requirements.txt`（含 `alembic==1.13.1`）。
2. 数据库连接配置与后端一致：
   - 默认读取 `backend/settings/.local.env`（`ENV=local`）或 `backend/settings/.prod.env`（`ENV=prod`）；
   - 或直接设置环境变量 `DATABASE_URL`（优先级最高）。
3. 所有命令在 `backend/` 目录下执行。`alembic.ini` 已配置 `prepend_sys_path = .`，
   裸 `alembic` 命令可直接 `import app`（无需 PYTHONPATH / `python -m alembic`）。

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
>
> ⚠️ **此方式隐含假设：存量库已具备完整 v1.2.0 schema**（所有表/字段/索引与
> `0001_initial_baseline` 一致）。若存量库缺少个别字段或约束，`stamp head` 会
> 让 Alembic 误以为基线已应用，后续新增迁移可能因底层对象缺失而失败。此类库应
> 先补齐与基线一致的 schema，或在迁移链中添加一次显式补齐迁移，再 `stamp head`。

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
