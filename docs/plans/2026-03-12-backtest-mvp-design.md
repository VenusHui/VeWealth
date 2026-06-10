# VeWealth 回测引擎 MVP 设计（方案1：轻量自研）

> **Status: ✅ Implemented** — Backtest engine is live with strategies, async jobs, drilldown details, and CSV export.

日期：2026-03-12  
分支：`dev/v1.1.0`

## 1. 目标

在现有 VeWealth 平台中新增可用的 A 股策略回测能力，第一阶段聚焦：

- 纯代码策略模板（内置）
- 同步执行回测
- 保存回测历史与结果
- 提供前端最小可用页面

## 2. 范围

### 2.1 本期包含

- 回测策略注册与查询
- 回测执行接口
- 回测历史列表与详情接口
- 回测记录入库
- A股关键执行约束（T+1、涨跌停、费用）

### 2.2 本期不包含

- 用户自定义代码上传执行
- 异步队列任务
- 参数网格搜索
- 复杂组合优化

## 3. 架构设计

新增后端模块：

- `app/schemas/backtest.py`
- `app/models/backtest.py`
- `app/routers/backtest.py`
- `app/services/backtest/`
  - `registry.py`
  - `engine.py`
  - `costs.py`
  - `metrics.py`
  - `strategies/base.py`
  - `strategies/ma_cross_v1.py`

前端新增：

- `frontend/app/backtest/page.tsx`
- `Navbar` 增加“策略回测”入口

## 4. API 设计

- `GET /api/backtest/strategies`
- `POST /api/backtest/run`
- `GET /api/backtest/runs`
- `GET /api/backtest/runs/{run_id}`

认证：复用现有 Bearer Token + `get_current_active_user`。

## 5. 数据模型

表：`backtest_runs`

主要字段：

- `user_id`, `name`, `status`
- `strategy_id`, `strategy_params`, `symbols`
- `start_date`, `end_date`, `initial_cash`, `benchmark`
- `cost_config`, `summary`, `equity_curve`, `trades`, `warnings`
- `error_message`, `created_at`, `updated_at`

存储策略：MVP 以 JSON 字段快速落地，后续按性能拆分明细表。

## 6. 执行规则（MVP）

- 仅做多
- 信号在 bar `t` 生成，`t+1` 开盘执行
- T+1：当日买入不可卖出
- 买入遇涨停、卖出遇跌停：不成交并记录 warning
- 交易成本：佣金（含最低佣金）、卖出印花税、双边滑点

## 7. 指标

输出 summary 指标：

- total_return
- annual_return
- max_drawdown
- sharpe
- win_rate
- profit_loss_ratio
- turnover
- total_trades

## 8. 后续路线

- M2：异步任务化（queued/running/cancel）
- M3：参数扫描与多次回测比较
- M4：导出报告与风险归因
