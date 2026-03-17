export type Strategy = {
  strategy_id: string
  name: string
  description: string
  param_schema: Array<{
    key: string
    label: string
    type: string
    default?: number | string
  }>
}

export type RunItem = {
  id: number
  name: string
  status: string
  strategy_id: string
  start_date: string
  end_date: string
  created_at: string
  summary?: Record<string, unknown>
}

export type DetailTab = 'overview' | 'trades' | 'rounds' | 'snapshots' | 'strategy'
export type MainTab = 'create' | 'records' | 'detail'

export type JobItem = {
  job_id: string
  status: string
  progress_pct?: number
}

export type BacktestOverview = {
  summary?: Record<string, unknown>
  equity_curve?: Array<{ datetime: string; equity: number }>
}

export type TradeRow = {
  datetime?: string
  symbol?: string
  side?: string
  price?: number
  qty?: number
  amount?: number
  fee?: number
  reason?: string
}

export type RoundRow = {
  symbol?: string
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
  qty?: number
  last_price?: number
  market_value?: number
  weight?: number
}

export type SnapshotRow = {
  snapshot_time?: string
  equity?: number
  cash?: number
  position_value?: number
  holdings?: SnapshotHolding[]
}

export type StrategyConfig = Record<string, unknown>

export type BacktestResult = {
  run_id?: number
  trades?: unknown[]
}
