/**
 * Pure computation helpers for the backtest panel.
 *
 * Kept free of React/UI concerns so they can be unit-tested in isolation.
 */
import type { SnapshotHolding, Strategy } from './components/types'

/**
 * Cast strategy parameter values into their numeric form when they look like
 * numbers, otherwise keep the raw string. Matches the payload the backend
 * expects for strategy params.
 */
export function parseStrategyParams(raw: Record<string, string>): Record<string, unknown> {
  const cast: Record<string, unknown> = {}
  Object.keys(raw).forEach((k) => {
    const val = raw[k]
    cast[k] = /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val
  })
  return cast
}

export interface StrategyParamValidation {
  valid: boolean
  errors: string[]
}

/**
 * 提交前前端内联校验：类型（非数值）、边界（min/max）、跨字段（short_window <
 * long_window）与被支持的回测模式。与后端 validate_strategy_params 保持同一套规则，
 * 尽量在提交前拦截，避免「合法参数稳定 0 命中」。对非数值/越界参数返回可读错误。
 */
export function validateStrategyParams(
  strategy: Strategy | undefined,
  raw: Record<string, string>,
  mode: string,
): StrategyParamValidation {
  const errors: string[] = []
  if (!strategy) return { valid: true, errors }

  const supported = strategy.supported_modes
  if (Array.isArray(supported) && supported.length > 0 && !supported.includes(mode)) {
    errors.push(`策略「${strategy.name}」不支持当前回测模式`)
  }

  strategy.param_schema.forEach((p) => {
    const val = raw[p.key] ?? ''
    if (p.type !== 'int' && p.type !== 'float') {
      return
    }
    if (val.trim() === '') {
      errors.push(`参数「${p.label}」不能为空`)
      return
    }
    const num = Number(val)
    if (Number.isNaN(num)) {
      errors.push(`参数「${p.label}」必须是数字`)
      return
    }
    if (typeof p.min === 'number' && num < p.min) {
      errors.push(`参数「${p.label}」不能小于 ${p.min}`)
    }
    if (typeof p.max === 'number' && num > p.max) {
      errors.push(`参数「${p.label}」不能大于 ${p.max}`)
    }
  })

  const shortStr = raw.short_window
  const longStr = raw.long_window
  if (shortStr !== undefined && longStr !== undefined && shortStr.trim() !== '' && longStr.trim() !== '') {
    const s = Number(shortStr)
    const l = Number(longStr)
    if (!Number.isNaN(s) && !Number.isNaN(l) && s >= l) {
      errors.push(`short_window(${s}) 必须小于 long_window(${l})`)
    }
  }

  return { valid: errors.length === 0, errors }
}

/** Format a symbol cell: "stockName (code)" when a name is available. */
export function fmtSymbolLabel(symbol?: string, stockName?: string): string {
  const code = symbol || '-'
  if (stockName) return `${stockName} (${code})`
  return code
}

/**
 * Normalize an equity curve to a per-date ratio against the first
 * positive equity value (used for chart comparison across runs).
 */
export function normalizeEquityByDate(
  rows: Array<{ trade_date: string; equity: number | string | null | undefined }>,
): Map<string, number> {
  const map = new Map<string, number>()
  const first = rows.find((x) => Number(x.equity) > 0)
  const base = first ? Number(first.equity) : 0
  for (const p of rows) {
    if (base > 0) map.set(p.trade_date, Number((Number(p.equity) / base).toFixed(6)))
  }
  return map
}

export interface ComparisonChartRow {
  trade_date: string
  main_norm: number | null
  benchmark_norm: number | null
  compare_norm: number | null
}

/**
 * Build the snapshot comparison chart rows by joining the normalized
 * main / benchmark / compare curves on trade date. Rows without a main
 * value are dropped (the chart always plots the main strategy).
 */
export function buildComparisonChartData(
  dates: string[],
  mainByDate: Map<string, number>,
  benchmarkByDate: Map<string, number>,
  compareByDate: Map<string, number>,
): ComparisonChartRow[] {
  return dates
    .map((d) => {
      const mainNorm = mainByDate.get(d)
      const benchmarkNorm = benchmarkByDate.get(d)
      const compareNorm = compareByDate.get(d)
      return {
        trade_date: d,
        main_norm: Number.isFinite(mainNorm as number) ? (mainNorm as number) : null,
        benchmark_norm: Number.isFinite(benchmarkNorm as number) ? (benchmarkNorm as number) : null,
        compare_norm: Number.isFinite(compareNorm as number) ? (compareNorm as number) : null,
      }
    })
    .filter((row) => row.main_norm != null)
}

/** Sort snapshot holdings: closed-today rows last (dimmed in the UI), then by market value desc. */
export function compareSnapshotHoldings(a: SnapshotHolding, b: SnapshotHolding): number {
  const sa = a.position_status === 'closed_today' ? 1 : 0
  const sb = b.position_status === 'closed_today' ? 1 : 0
  if (sa !== sb) return sa - sb
  return Number(b.market_value || 0) - Number(a.market_value || 0)
}

/** Clamp an index into [0, max]. */
export function clampIndex(idx: number, max: number): number {
  return Math.min(Math.max(idx, 0), max)
}

/** Clamp a progress percentage into [0, 100], falling back when falsy. */
export function clampPct(value: number | string | null | undefined, fallback = 0): number {
  const n = Number(value || fallback)
  return Math.min(Math.max(n, 0), 100)
}
