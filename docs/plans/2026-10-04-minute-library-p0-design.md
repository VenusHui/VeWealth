# 分钟级回测 P0 设计：本地分钟库 + 每日增量采集

> **Status: ✅ Implemented** — 存储选型、`period` 列迁移、全市场采集器、每日调度任务均已落地。
> 对应 issue：VEW-64（父 issue，子 issue VEW-65 回填 / VEW-66 引擎改造）

日期：2026-10-04

## 背景

外部数据源都给不了跨年分钟历史（VEW-61 实测：mootdx 1min ≈ 4 个月、腾讯 `ifzq` m1
≈ 18 个交易日、东财单次 5 天、Tushare `stk_mins` 1 次/小时）。**长期方案只能是本地
累积**，采购 / 提额是可选加速项。P0 是整条链路里唯一「现在不做、以后补不回来」的
动作，成本近乎为零，故优先落地。

## 存储选型：Parquet 按 `period` / `trade_date` 分区

| 方案 | 判断 |
|---|---|
| **Parquet 分区（选定）** | 列存 + 目录/列裁剪，回测按「周期 + 日期区间」扫描最贴合；`pd.read_parquet` 直接进 pandas 链路；幂等 = 读旧文件 + 去重合并 + 原子替换，不需要事务与冲突键；纯文件系统，无新增实例 |
| PG 分区表 | 3.47 亿行/年量级下是百 GB 级行存 + 索引维护 + VACUUM 成本；跨年区间扫描要走大范围索引扫描再拼装 |
| TimescaleDB | 需要新增扩展与运维面，且收益主要在时序聚合而非「按日期区间整段扫描」这一回测主路径 |

**职责边界**（重要，避免双写）：

- **Parquet 分钟库 = 归档与回测的事实源**（全市场、跨年、所有周期）。
- **PG `stock_minute_data` = 在线查询的近期窗口**（分时图 / 告警 / 选股信号），只采
  自选股、行数小。全市场跨年数据**不写 PG** —— 那正是本选型要避免的量级。

目录布局：`{root}/period=1/trade_date=2026-10-02/bars.parquet`（一天一个文件，
当日全市场）。不按标的分文件：全市场单日 5921 个小文件会压垮文件系统元数据，也让
回测扫描退化成 5921 次 `open`。

## 实测容量与耗时基线（本机，合成全市场 1min 单日 142 万行）

| 项 | 实测 | 立项估算 | 说明 |
|---|---|---|---|
| 单分区文件 | **53.4 MB**（zstd，≈39 B/row） | — | snappy 57.0 / brotli 51.1 / 不压缩 58.3 |
| 年容量（1min 全市场） | **≈12.7 GB/年** | 4–6 GB/年 | 估算按 15–20 B/row，实际 float64 OHLCV ≈39 B/row |
| 写盘（整天，12 次 flush） | **4–6 s** | — | 相对取数可忽略 |
| 读回（整天） | **0.1–0.3 s** | — | |
| 取数（全市场单日 1min） | **≈40–60 min**（推算） | 2.6 min（串行） | 见下 |

**关于取数成本的修正**：立项的 2.6 min 意味着 26 ms/标的，低于任何 TDX 往返的物理
下限。代码自身的实测数据（VEW-60：20 标的 × 250 根，加锁 12.0 s，含镜像探测）折算
约 0.3–0.6 s/标的，全市场 5921 标的 ≈ **40–60 分钟**。且 VEW-60 的取数锁是全局串行
点，**并发不改变网络取数串行的事实**，线程只摊薄建连与解析开销。

结论：40–60 min 仍完全落在夜间窗口内（15:00 收盘 → 21:00 采集 → 次日开盘前），
P0 设计成立；但容量与耗时规划应按修正后的数字，而不是 2.6 min。采集器已把
`elapsed_sec` / `avg_fetch_sec` / `p95_fetch_sec` 写进每次运行结果，首次生产运行即可
确认真实值。

> 本次无法给出真实取数耗时：沙箱到 TDX 镜像的网络不通（curated 6 个镜像仅 1 个
> 接受 TCP，且不回 K 线 body），东财被拦截，故取数侧只有推算值。写盘/读回为实测。

## 实现

### 1. `stock_minute_data` 补 `period` 列（迁移 `0004_add_minute_period`）

唯一键 `(stock_code, trade_time)` → `(stock_code, period, trade_time)`。必须改键：
1min 与 5min 在 09:35 这类时点上 `trade_time` 相同，旧键会把两个周期判成重复行、
批量 upsert 时互相覆盖。新增 `(period, trade_date)` 复合索引供按周期扫描。存量数据
全部是 1min，`server_default='1'` 直接回填。

读路径（分时图 / 告警 / 选股信号）同步加上 `period == '1'` 限定 —— 表内可存多周期
后，不限定周期会把不同粒度混进同一条序列。

### 2. 本地分钟库 `app/services/minute_store.py`

- 写入契约：`(stock_code, period, trade_time)` 为幂等键，重复写入**新数据胜出**；
  写盘原子（同目录临时文件 + `os.replace`），读者看不到半个文件。
- `trade_date` 存 date32（常量列，timestamp64 白付 4 B/row），`read_bars` 读出来转
  回 datetime64，消费方不必处理 object dtype。
- flush 是「读旧分区 + 去重合并 + 整文件重写」，因此按 500 标的 flush 12 次时累计
  写入量约为最终分区的 7 倍 —— 实测总耗时仍在 4–6 s，相对取数可忽略，故保留较小的
  flush 窗口（崩溃时丢的进度少）。
- 单写者假设：同进程内由模块级锁串行化；跨进程并发写同一分区不在支持范围（采集是
  单一调度任务，P1 回填同理）。
- `read_bars` 只用于单日 / 短区间：跨年区间不能一次读（3.5 亿行），P2 引擎应按
  `available_dates` + `partition_path` 逐分区迭代。

### 3. 全市场采集器 `app/services/minute_collector.py`

- 股票池：当日（或更早最近）universe 快照，**全板块 + 含 ST**（ST / 退市由回测按
  as_of 过滤，采集侧提前过滤会留下补不回的空洞）；无快照降级到当前维表，维表为空
  落到静态清单。
- 并发：线程池取数，**依赖 VEW-60 的取数锁**，不得绕过它开裸并发（TDX 共享 client
  并发会静默返回空）。
- 断点续采：每个 `(period, trade_date)` 一份 JSON 日志记 `completed` / `empty` /
  `failed`；重跑跳过前两者、重试 failed，并与库内已落盘标的取并集（日志写失败也不
  重复采）。`force=True` 全量重采。
- 只保留落在目标交易日的 bar：备源（东财 trends2）返回跨日窗口，不裁剪会把别的日期
  的数据标成今天。
- 耗时统计：`elapsed_sec` / `avg_fetch_sec` / `p95_fetch_sec` 随结果返回，供容量校准。

### 4. 调度：第 5 个 job

`minute_data_collection`，cron `0 21 * * 1-5`（`MINUTE_COLLECT_CRON`），排在日线
采集（20:00）与 universe 快照（20:40）之后，保证当日股票池快照已落盘。独立开关
`MINUTE_COLLECT_ENABLED`。

### 5. 在线表批量 upsert

`DataCollector` 的逐行 `query().first()` 查重（N+1）改为按唯一键批量 upsert：
PG 走 `ON CONFLICT DO UPDATE`，其它方言退化为「一次查出已存在 key，只插新增」。

## 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `MINUTE_LIBRARY_DIR` | `data/minute_bars` | 容器内 `/app/data` 是持久卷 `vewealth-backend-data`，重部署不丢 |
| `MINUTE_COLLECT_ENABLED` | `True` | 独立开关 |
| `MINUTE_COLLECT_CRON` | `0 21 * * 1-5` | 收盘后 |
| `MINUTE_COLLECT_PERIODS` | `1` | 逗号分隔，如 `1,5` |
| `MINUTE_COLLECT_WORKERS` | `20` | 并发取数线程 |
| `MINUTE_COLLECT_FETCH_BUDGET` | `20.0` | 单标的取数墙钟预算（秒） |
| `MINUTE_COLLECT_FLUSH_EVERY` | `500` | 每 N 标的落盘一次并推进断点 |

## 验证

- `pytest tests/test_minute_library.py`（21 例）：幂等重写、新数据胜出、原子落盘、
  周期隔离、断点续采、失败重试、跨日裁剪、并发完整性、唯一键含 period、迁移链。
- `pytest tests` 全量 308 通过；`black --check .` 通过。
- 迁移离线渲染确认：`alembic upgrade 0003:0004 --sql` 产出
  `ADD COLUMN period ... DEFAULT '1' NOT NULL` + 唯一键重建 + 新复合索引。

## 遗留 / 交给后续阶段

- **P1（VEW-65）**：历史回填。复用本库的写入契约与幂等键；注意腾讯 volume 是「手」、
  mootdx 是「股」，回填前必须统一（差 100 倍）。
- **P2（VEW-66）**：引擎按分区迭代读取，不要把跨年区间一次读进内存。
- 容量：按 **13 GB/年/周期** 规划磁盘；`float32` 价格列可再省 ~30%，但会引入精度
  误差、破坏跨源严格比对，暂不采用。
- 备份：`data/minute_bars/` 目前只靠 Docker 命名卷，**未纳入备份策略**；建议后续加
  快照/异地副本（与日线数据同级）。
