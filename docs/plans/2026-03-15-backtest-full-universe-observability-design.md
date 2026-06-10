# VeWealth 回测改造设计：全市场强制扫描 + 结果可解释性

> **Status: ✅ Implemented** — Full universe scanning is default, diagnostics fields are in results, static stock pool script exists.

日期：2026-03-15

## 背景

用户在 `strategy_select` 模式下遇到“回测成功但 0 笔交易”，且无法区分是“策略没信号”还是“行情数据缺失”。同时希望取消“最大扫描数量”参数，默认强制扫描全市场。

## 目标

1. `strategy_select + universe_type=all` 固定全市场扫描（不再允许前端传限量参数）。
2. 保留宽松模式：即使缺数据较多，任务仍可成功返回。
3. 强化可解释性：结果中提供数据覆盖和信号命中诊断统计。
4. 补齐静态股票池文件，并提供每周刷新能力。

## 方案

### 1) 参数与策略定义

- 删除 `volume_shrink_drop_v1` 的 `max_universe_size` 参数定义。
- 后端在 `all` 模式下不再读取任何限量参数，直接调用 `get_all_stock_symbols()` 全量拉取。

### 2) 回测结果诊断字段

在 `strategy_select` 结果里新增：

- `universe_size`
- `data_available_count`
- `data_empty_count`
- `signal_hit_count`

并在 warning 中补充“无可用日线数据”的数量和样例代码（截断预览）。

### 3) 静态股票池补全与维护

- 使用 AKShare `stock_info_a_code_name` 一次性生成全量 `a_share_symbols.txt`。
- 新增脚本 `backend/scripts/refresh_a_share_symbols.py`，支持重复执行刷新。
- 在 README 增加每周 cron 示例。

## 预期收益

- 用户可一眼判断 0 笔交易的真实原因。
- 全市场扫描行为与用户预期一致。
- 实时接口失败时，静态兜底池具备可维护性。
