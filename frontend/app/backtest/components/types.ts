export const BOARD_LABELS: Record<'main' | 'gem' | 'star' | 'bse', string> = {
  main: '主板',
  gem: '创业板',
  star: '科创板',
  bse: '北交所',
}

export type Strategy = {
  strategy_id: string
  name: string
  description: string
  param_schema: Array<{
    key: string
    label: string
    type: string
    default?: number | string
    min?: number
    max?: number
  }>
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
