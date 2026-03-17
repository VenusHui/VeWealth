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
  summary?: Record<string, any>
}

export type DetailTab = 'overview' | 'trades' | 'rounds' | 'snapshots' | 'strategy'
export type MainTab = 'create' | 'records' | 'detail'

export type JobItem = {
  job_id: string
  status: string
  progress_pct?: number
}
