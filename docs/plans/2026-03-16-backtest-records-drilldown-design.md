# Backtest 记录与详情拆分设计（方案3）

- 日期：2026-03-16
- 负责人：Assistant + 胡锦晖确认
- 结论：采用 **三级钻取（方案3）**，并在明细层采用 **成交 + 回合交易（B）**。

## 1. 目标

在不破坏现有回测执行链路（/api/backtest/jobs）的前提下，实现：

1. 回测记录页默认展示任务级摘要（A）。
2. 点击记录后进入详情，展示资金曲线、逐笔成交、回合交易、策略配置。
3. 为后续持仓快照和更细粒度归因分析预留接口。

## 2. 信息架构

### P1 回测记录列表页

字段：
- run_id/name
- strategy_id
- date range
- status
- total_return/max_drawdown 等摘要
- created_at

交互：
- 点击行加载详情（P2/P3）

### P2 概览页

模块：
- 核心指标卡（summary）
- 资金曲线（equity_curve）
- 风险提示（warnings）

### P3 主题子页

1. 成交明细（Trades）
2. 回合交易（Rounds，开平配对）
3. 持仓快照（Snapshots，当前版本接口保留）
4. 策略配置（Strategy Config）

## 3. 后端接口设计

新增：
- `GET /api/backtest/runs/{run_id}/overview`
- `GET /api/backtest/runs/{run_id}/trades`
- `GET /api/backtest/runs/{run_id}/rounds`
- `GET /api/backtest/runs/{run_id}/snapshots`
- `GET /api/backtest/runs/{run_id}/strategy-config`

保留：
- `GET /api/backtest/runs`
- `GET /api/backtest/runs/{run_id}`
- 任务接口 `/api/backtest/jobs*`

## 4. 数据策略

当前数据库模型 `backtest_runs` 已包含：
- summary
- equity_curve
- trades
- strategy_params/cost_config

因此本次先做“**读优化与视图拆分**”：
- 不立即拆分 6 张新明细表，降低首期改造风险。
- `rounds` 通过 `trades` 在服务层实时配对计算（FIFO）生成。
- `snapshots` 先提供接口与空返回兼容，后续回测写入阶段补齐。

## 5. 前端实现

在 `/backtest` 页面内完成“记录列表 + 详情 Tab”结构：
- 记录列表（任务级摘要）
- 详情 Tab：概览、成交、回合、快照、策略参数

这样达到“模块内分子页面”的体验，同时减少路由复杂度。

## 6. 实现补充（2026-03-16 二次迭代）

已补齐：
1. 回测执行结果持仓快照：
   - 通过 `run_for_symbol` 产出 `position_curve`（每个时点持仓、现金、权益）
   - 服务层聚合为组合级 `positions_snapshot` 并写入 run.summary
2. 快照页可视化：
   - 前端 snapshots 子页按日期展示快照卡片
   - 每个快照下展示持仓明细（标的/数量/现价/市值/权重）
3. CSV 导出：
   - `GET /api/backtest/runs/{run_id}/trades/export`
   - `GET /api/backtest/runs/{run_id}/rounds/export`

## 7. 风险与后续

风险：
- 历史 run（改造前数据）仍可能没有快照。
- 回合交易为服务层推导值，和未来引擎原生 round 口径可能存在微差。

后续里程碑：
1. 引入 round_trades 持久化表，避免每次在线计算。
2. 支持导出筛选条件（按 symbol/date 区间）。
3. 增加快照分页与按日期筛选。
