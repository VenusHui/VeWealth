# 全市场回测扫描与运行观测

> 关联设计：`docs/plans/2026-03-15-backtest-full-universe-observability-design.md`

VeWealth 复用股票池（`security_universe` 维表 / 静态兜底清单）与策略选股器（`strategy_select` 模式），可对全市场执行回测扫描，并通过「运行观测」入口查看任务运行状态与扫描结果诊断。

## 触发全市场回测扫描

通过异步任务接口 `POST /api/backtest/jobs`，使用 `strategy_select` 模式 + `universe_type=all`：

```json
{
  "name": "全市场扫描-主板-排除ST",
  "strategy_id": "ma_cross_v1",
  "mode": "strategy_select",
  "universe_type": "all",
  "strategy_params": {
    "boards": ["main"],
    "exclude_st": true,
    "hold_days": 5,
    "policy_profile": "default"
  },
  "start_date": "2026-01-01",
  "end_date": "2026-08-24",
  "initial_cash": 1000000
}
```

要点：

- `universe_type=all` 时后端固定拉取全市场股票池，忽略任何限量参数。
- 选股池为空时任务直接失败并给出提示（可改用 `universe_type=custom` + `pool_symbols` 手工兜底）。
- 也可通过 `POST /api/backtest/run` 同步执行（不推荐全市场场景，扫描耗时较长）。

## 运行观测入口

### 1. 观测面板聚合接口

`GET /api/backtest/observability` 一次返回观测面板所需的完整视图：

```json
{
  "success": true,
  "data": {
    "generated_at": "2026-08-24T12:00:00",
    "universe": {
      "total_active": 5000,
      "st_active": 120,
      "non_st_active": 4880,
      "by_board": { "main": 3000, "gem": 1300, "star": 550, "bse": 150 },
      "defaults": { "boards": ["main"], "exclude_st": true }
    },
    "active_jobs": [
      {
        "job_id": "abc123",
        "name": "全市场扫描-主板",
        "strategy_id": "ma_cross_v1",
        "status": "running",
        "stage": "scanning",
        "progress_pct": 42.0,
        "total_symbols": 5000,
        "processed_symbols": 2100,
        "created_at": "2026-08-24T10:00:00"
      }
    ],
    "recent_scan_runs": [
      {
        "run_id": 101,
        "name": "全市场扫描-主板",
        "strategy_id": "ma_cross_v1",
        "status": "completed",
        "diagnostics": {
          "universe_size": 5000,
          "data_available_count": 4800,
          "data_empty_count": 200,
          "candidate_count": 96,
          "selected_count": 20,
          "event_count": 18,
          "policy_profile": "default"
        },
        "warnings": ["无可用日线数据股票数: 200/5000，示例: ..."]
      }
    ],
    "counters": {
      "jobs": { "pending": 0, "running": 1, "success": 12, "failed": 0, "cancelled": 1 },
      "runs": { "total": 20, "recent_scan_count": 6 }
    }
  }
}
```

字段含义：

| 字段 | 说明 |
| --- | --- |
| `universe` | 股票池覆盖统计（总量 / ST / 非 ST / 分板块） |
| `active_jobs` | 进行中的回测任务（pending / running），含进度、阶段、已扫描数量 |
| `recent_scan_runs` | 最近完成且带诊断字段的全市场扫描记录（`strategy_select` 运行） |
| `counters.jobs` | 回测任务按状态计数（任务生命周期汇总） |
| `counters.runs` | 回测记录总数与最近窗口内扫描记录数 |

### 2. 单任务 / 单记录观测

- `GET /api/backtest/jobs`、`GET /api/backtest/jobs/{job_id}` — 任务运行状态、进度、阶段、错误信息；任务成功后 `result` 内含完整扫描结果与 `diagnostics`。
- `GET /api/backtest/runs`、`GET /api/backtest/runs/{id}/overview` — 已完成的回测记录；`strategy_select` 扫描的 `summary.diagnostics` 与顶层 `diagnostics` 提供扫描诊断（股票数、有效行情数、空数据数、候选/命中数），`warnings` 提供「无可用日线数据」等提示。

## 诊断字段说明

`strategy_select` 模式扫描结果中的 `diagnostics`：

| 字段 | 含义 |
| --- | --- |
| `universe_size` | 参与扫描的股票池总数 |
| `data_available_count` | 成功拉取且满足策略数据长度要求的股票数 |
| `data_empty_count` | 无可用日线数据（或数据过短）被跳过的股票数 |
| `candidate_count` | 生成候选信号的股票数 |
| `ranked_count` / `selected_count` / `ordered_count` | 策略 policy pipeline 各阶段数量 |
| `event_count` | 最终可执行事件数 |
| `policy_profile` | 采用的策略配置档位 |
| `effective_universe_filter` | 实际生效的板块 / ST 过滤条件 |

当 `event_count` 为 0 时，请先看 `data_empty_count`（行情缺失）与 `warnings`（缺列 / 无信号提示），区分「策略无信号」与「行情数据缺失」。

## 股票池维护

股票池优先读 `security_universe` 维表，为空时回退 `backend/data/a_share_symbols.txt`。刷新静态清单：

```bash
python backend/scripts/refresh_a_share_symbols.py
```

建议每周刷新一次（cron 示例见 README「静态A股股票池维护」）。
