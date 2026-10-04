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
| 年容量（1min 全市场） | **≈12.7 GB/年/周期** | 4–6 GB/年 | 估算按 15–20 B/row，实际 float64 OHLCV ≈39 B/row |
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
确认真实值（**待办**：拿到真实值后回填本节并复核夜间窗口余量）。

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

- 写入契约：`(stock_code, period, trade_time)` 为幂等键，重复写入**新数据胜出**
  （先去重再排序，不依赖排序稳定性）；写盘原子（同目录临时文件 + `os.replace`），
  读者看不到半个文件。
- **坏分区不按空分区处理**（评审 M2）：分区存在但读不出来时抛
  `MinutePartitionError`。写路径拒绝覆盖 —— 覆盖会把该分区里其它标的的 bar 静默抹掉，
  而断点日志里它们已是 `completed`，重跑补不回，属不可恢复丢失；读路径拒绝静默跳过
  —— 那等于把缺了一整天的结果当完整结果交给回测。`covered_symbols` 同理（返回空集
  会让整天被当成未采、白跑一轮 40–60 min）。恢复方式：人工移走坏文件后该日
  `force=True` 重采；`stats()` 会列出损坏分区。
- `trade_date` 存 date32（常量列，timestamp64 白付 4 B/row），`read_bars` 读出来转
  回 datetime64，消费方不必处理 object dtype。
- flush 是「读旧分区 + 去重合并 + 整文件重写」，因此按 500 标的 flush 12 次时累计
  写入量约为最终分区的 7 倍 —— 实测总耗时仍在 4–6 s，相对取数可忽略，故保留较小的
  flush 窗口（崩溃时丢的进度少）。
- 单写者假设：同进程内由模块级锁串行化；跨进程并发写同一分区不在支持范围（采集是
  单一调度任务，P1 回填同理）。
- `read_bars` 只用于单日 / 短区间：跨年区间不能一次读（3.5 亿行），P2 引擎应按
  `available_dates` + `partition_path` 逐分区迭代。
- `stats()` 是**预留接口，尚未接入任何端点**：`/api/health` 是 docker healthcheck 每
  30s 打一次的路径，而统计行数要读全部分区，不适合放进健康检查。

### 3. 全市场采集器 `app/services/minute_collector.py`

- 股票池：当日（或更早最近）universe 快照，**全板块 + 含 ST**（ST / 退市由回测按
  as_of 过滤，采集侧提前过滤会留下补不回的空洞）；无快照降级到当前维表，维表为空
  落到静态清单。
- 并发：线程池取数，**依赖 VEW-60 的取数锁**，不得绕过它开裸并发（TDX 共享 client
  并发会静默返回空）。线程数默认 **4**（原 20）：取数本身被锁串行化，加线程只是在锁上
  排队，而源级探针等锁超时是 5s（`_MOOTDX_PROBE_LOCK_TIMEOUT`）——20 个线程排队时
  探针平均要等 ~12s，会在整个采集窗口内一直拿不到锁，使镜像熔断/恢复（VEW-62）失去
  健康信号；4 个线程的排队期望 ~2.4s，探针可用。这是已知盲区，故不调高。
- 断点续采：每个 `(period, trade_date)` 一份 JSON 日志记 `completed` / `empty` /
  `no_session` / `failed`；重跑**只跳过** `completed` 与 `empty`，`no_session` 与
  `failed` 都重试，并与库内已落盘标的取并集（日志写失败也不重复采）。`force=True`
  全量重采。
- **空结果分类**（评审 M1/M3）：取数返回空有两种成因，后果相反 —— 源故障（镜像静默
  返回空）记成终态会让整天数据在重跑时被永久跳过；标的停牌则重试无意义。因此每轮取数
  前先跑**源级探针**（同一条 provider 路径、含取数锁），三态判定：
  - 样本股在目标日取到 bar（`ok`）→ 源可用的**正面证据**，空 = 该标的当日无数据，
    记 `empty`（终态）；
  - 目标日取不到、回看 15 个日历日内更早的交易日取到（`no_session`）→ 记
    `no_session`，**可重试**，不打 ERROR；
  - 两者都取不到（`down`）→ 源故障，本轮空结果全部记 `failed`（可重试）并打 ERROR。
  - **`no_session` 为什么不能记终态**（M3）：它只说明「源能给历史、给不了今天」，而这
    正是镜像缓存滞后一天（或当日数据未上架）+ 备源被限流的形态 —— 在一个**真实交易日**
    上会命中 NO_SESSION，此时批量取数遇到同一个源状态、全市场几乎全空。若记终态，重跑
    一律 `requested=0`，这一天在回测事实源里永久缺失，而日志讲的是一个可信的故事
    （「判定为非交易日」）。反过来说，「记终态以免节假日重取」也不成立：没有自动重跑
    机制、调度只针对 `date.today()`，节假日那天的记录永远不会被自动重取，故记可重试
    成本为零。单列一桶的收益是节假日报 `no_session=5921`（不打 ERROR、不污染 `failed`），
    而源滞后那天变成「重跑可补回」。
  - 探针仍是**交易日历的替代物**（仓库内无交易日历，cron 在节假日照常触发），但只用于
    **分类展示**、不再决定终态，误判代价被限制在观感上。回看窗口 15 天覆盖含相邻周末
    的国庆（8 天）/ 春节（8–9 天）长假 —— 窗口过短会让长假尾部的日期误判成 `down`
    并打出假的源故障 ERROR。
  - 兜底：探针健康但空结果占**全市场**过半（`MINUTE_COLLECT_EMPTY_RATIO_LIMIT`）时，
    认为源只坏了一部分（探针恰好落在好的那部分），同样记可重试失败。分母用全市场规模
    而非本轮待采规模，避免续采轮（待采集合可能只剩少量停牌股）被误判成源故障。
  - 判据偏向「可疑就重试」：只有拿到 `ok` 这个正面证据才认终态；记错方向的代价不对称
    （静默丢一天 vs 多跑一轮取数）。
- 只保留落在目标交易日的 bar：备源（东财 trends2）返回跨日窗口，不裁剪会把别的日期
  的数据标成今天。
- 耗时统计：`elapsed_sec` / `avg_fetch_sec` / `p95_fetch_sec` 随结果返回，供容量校准；
  源探针判定值 `source_probe` 一并返回并在调度日志里打印。

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
| `MINUTE_COLLECT_ENABLED` | `True` | 默认开启，理由见「上线步骤」第 2 条 |
| `MINUTE_COLLECT_CRON` | `0 21 * * 1-5` | 收盘后 |
| `MINUTE_COLLECT_PERIODS` | `1` | 逗号分隔，如 `1,5` |
| `MINUTE_COLLECT_WORKERS` | `4` | 并发取数线程（**不要调高**，见上文探针盲区） |
| `MINUTE_COLLECT_FETCH_BUDGET` | `20.0` | 单标的取数墙钟预算（秒） |
| `MINUTE_COLLECT_FLUSH_EVERY` | `500` | 每 N 标的落盘一次并推进断点 |
| `MINUTE_COLLECT_EMPTY_RATIO_LIMIT` | `0.5` | 单轮空结果占全市场比例上限，超过即按源异常处理（可重试） |

## 上线步骤（发布动作）

1. **先迁移、后起容器**：`0004_add_minute_period` 给 `stock_minute_data` 加 `period`
   列并重建唯一键。应用启动路径本身会跑 `alembic upgrade head`（`init_db()`，见
   `backend/app/core/database.py`），迁移失败会直接阻止启动，所以容器化部署不会出现
   「新代码 + 旧 schema」；手工/裸机部署则必须先执行 `alembic upgrade head`。
2. **采集任务默认开启**（`MINUTE_COLLECT_ENABLED=True`）：分钟库是「现在不采、以后补不
   回来」的数据，每个交易日漏采即永久缺失 —— 这正是本 issue 立项要防的结果，所以交付物
   默认生效。首轮请有人看日志确认 `elapsed_sec`（夜间窗口余量）与磁盘余量
   （≈13 GB/年/周期）；幂等可重入、断点续采、`source_probe` / `failed` / `no_session` /
   `elapsed_sec` 均已进日志，无人值守失败也不会损坏已有数据。

   想先手工验证一轮（不依赖调度）时：

   ```bash
   docker exec vewealth-backend python -c "
   from app.services.minute_collector import MinuteCollector
   from app.core.database import SessionLocal
   from datetime import date
   db = SessionLocal()
   print(MinuteCollector(db).collect('1', date(2026,10,2)).as_dict())
   db.close()"
   ```

   期望：`source_probe=ok`、`failed` 为 0 或仅停牌标的、`elapsed_sec` 落在夜间窗口内。
   若团队惯例是风险动作先关着，可置 `False`（写进 `backend/settings/.prod.env` 后重启
   后端），但**必须同时指定谁在何时打开**，否则「临时关着」会静默变成「一直没开」。

## 验证

- `pytest tests/test_minute_library.py`（32 例）：幂等重写、新数据胜出、原子落盘、
  周期隔离、断点续采、失败重试、跨日裁剪、并发完整性、唯一键含 period、迁移链、
  坏分区拒绝覆盖 / 拒绝静默跳过、源故障下空结果可重试、疑似休市可重试（M3：源滞后
  那天能被重跑补回）、长假回看窗口、空结果占比异常的兜底判定。
- `pytest tests` 全量通过；`black --check .` 通过。
- 迁移离线渲染确认：`alembic upgrade 0003:0004 --sql` 产出
  `ADD COLUMN period ... DEFAULT '1' NOT NULL` + 唯一键重建 + 新复合索引。

## 遗留 / 交给后续阶段

- **P1（VEW-65）**：历史回填。复用本库的写入契约与幂等键；注意腾讯 volume 是「手」、
  mootdx 是「股」，回填前必须统一（差 100 倍）。
- **P2（VEW-66）**：引擎按分区迭代读取，不要把跨年区间一次读进内存。
- 容量：按 **13 GB/年/周期** 规划磁盘（多周期线性叠加）；`float32` 价格列可再省 ~30%，
  但会引入精度误差、破坏跨源严格比对，暂不采用。
- 备份：`data/minute_bars/` 目前只靠 Docker 命名卷，**未纳入备份策略**；建议后续加
  快照/异地副本（与日线数据同级）。这也是采集任务首次上线默认关闭的原因之一。
- 交易日历：目前用「源探针 + 回看 15 天」**只做分类**（`no_session` 可重试，误判不丢
  数据）。若后续引入正式交易日历（含临时休市），可改为直接查日历、整轮跳过节假日，
  省掉回看探针并把节假日那轮从「疑似」变成确定。
- 首轮运行：需要有人在旁边看日志确认 `elapsed_sec` 与磁盘余量（见「上线步骤」第 2 条），
  并把真实耗时回填本文档。
