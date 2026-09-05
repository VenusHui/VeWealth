export const BOARD_LABELS: Record<'main' | 'gem' | 'star' | 'bse', string> = {
  main: '主板',
  gem: '创业板',
  star: '科创板',
  bse: '北交所',
}

export type StrategyParamField = {
  key: string
  label: string
  type: string
  default?: number | string
  min?: number
  max?: number
  /** 可选项（枚举参数），backend 契约 VEW-24 起提供。 */
  options?: string[]
  /** 展示分组（signal / portfolio），backend 未提供时前端按 key 兜底分类。 */
  group?: string
  /** 数值单位（如 日 / % / 0~1），用于表单中文帮助。 */
  unit?: string
  /** 参数说明文案，用于表单帮助。 */
  help?: string
}

export type Strategy = {
  strategy_id: string
  name: string
  description: string
  param_schema: StrategyParamField[]
  usable: boolean
  unusable_reasons: string[]
  supported_modes: string[]
  min_history_bars?: number
  signal_timestamp?: string
  score_definition?: string
  score_range?: [number, number] | null
  exit_rule?: string
}

export type RunItem = {
  id: number
  name: string
  status: string
  strategy_id: string
  start_date: string
  end_date: string
  created_at: string
  summary?: {
    total_return?: number | string
    max_drawdown?: number | string
    [key: string]: unknown
  }
}

export type DetailTab = 'overview' | 'trades' | 'rounds' | 'snapshots' | 'strategy'
export type MainTab = 'create' | 'records' | 'detail' | 'strategies'
export type ActiveJobStatus = 'pending' | 'running'
export type JobStatus = 'pending' | 'running' | 'success' | 'completed' | 'failed' | 'cancelled'

export type JobItem = {
  job_id: string
  name?: string
  status: string
  progress_pct?: number
  created_at?: string
}

export type BacktestOverview = {
  summary?: Record<string, unknown>
  equity_curve?: Array<{ datetime: string; equity: number }>
}

export type TradeRow = {
  datetime?: string
  symbol?: string
  stock_name?: string
  side?: string
  price?: number
  qty?: number
  amount?: number
  fee?: number
  reason?: string
}

export type RoundRow = {
  symbol?: string
  stock_name?: string
  open_time?: string
  open_price?: number
  close_time?: string
  close_price?: number
  holding_days?: number | null
  pnl_ratio?: number
  pnl_amount?: number
  exit_reason?: string
}

export type SnapshotHolding = {
  symbol?: string
  stock_name?: string
  qty?: number
  last_price?: number
  market_value?: number
  weight?: number
  position_status?: 'holding' | 'closed_today' | string
  trade_date?: string
}

export type SnapshotRow = {
  snapshot_time?: string
  equity?: number
  cash?: number
  position_value?: number
  holdings?: SnapshotHolding[]
}

export type BacktestFacts = {
  summary?: Record<string, unknown>
  equity_curve_daily?: Array<{
    trade_date: string
    equity: number
    daily_return?: number | null
  }>
  benchmark_curve_daily?: Array<{
    trade_date: string
    value_raw?: number
    value_norm?: number | null
  }>
  benchmark_meta?: {
    benchmark_code?: string
    source_type?: 'tr' | 'price' | string
    source_note?: string | null
  } | null
  compare_run_curve_daily?: Array<{
    trade_date: string
    value_raw?: number
    value_norm?: number | null
  }>
  compare_run_meta?: {
    run_id?: number
    run_name?: string
  } | null
  positions_daily_eod?: Array<{
    trade_date: string
    symbol?: string
    stock_name?: string
    qty?: number
    last_price?: number
    market_value?: number
    weight?: number
    position_status?: 'holding' | 'closed_today' | string
  }>
  instrument_meta?: Array<{ symbol?: string; stock_name?: string }>
  data_quality?: {
    missing_equity_dates?: string[]
    missing_snapshot_dates?: string[]
  }
}

export type StrategyConfig = Record<string, unknown>

/** 成交成本配置，字段与 backend `CostConfig` 契约一一对应。 */
export type CostConfig = {
  commission_rate: number
  min_commission: number
  stamp_tax_rate: number
  slippage_rate: number
}

export const DEFAULT_COST_CONFIG: CostConfig = {
  commission_rate: 0.0003,
  min_commission: 5,
  stamp_tax_rate: 0.001,
  slippage_rate: 0.0005,
}

export type BoardKey = 'main' | 'gem' | 'star' | 'bse'

/** `/api/backtest/universe/stats` 返回的股票池统计，用于提交前预估扫描数量。 */
export type UniverseStats = {
  total_active?: number
  st_active?: number
  non_st_active?: number
  by_board?: Record<BoardKey, number>
  by_board_exclude_st?: Record<BoardKey, number>
  defaults?: { boards?: BoardKey[]; exclude_st?: boolean }
}

export type BenchmarkOption = { label: string; value: string }

export const BENCHMARK_OPTIONS: BenchmarkOption[] = [
  { label: '上证综指', value: '000001.SH' },
  { label: '深证成指', value: '399001.SZ' },
  { label: '创业板指', value: '399006.SZ' },
  { label: '沪深300', value: '000300.SH' },
]

export type BacktestResult = {
  run_id?: number
  trades?: unknown[]
}

export type StrategyLatestBacktest = {
  run_id: number
  run_name: string
  created_at: string
  annual_return?: number | null
  total_return?: number | null
  sharpe?: number | null
  max_drawdown?: number | null
}

export type StrategyManagementListItem = {
  strategy_id: string
  name: string
  description: string
  usable: boolean
  policy_profile?: string | null
  last_modified_at?: string | null
  latest_backtest?: StrategyLatestBacktest | null
  has_code: boolean
}

export type StrategyManagementDetail = {
  strategy_info: StrategyManagementListItem
  latest_backtest?: StrategyLatestBacktest | null
  code: {
    language: string
    source_path?: string | null
    core_snippet?: string | null
    full_source?: string | null
    line_count: number
  }
}
