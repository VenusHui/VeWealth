/**
 * Pure computation helpers for the backtest panel.
 *
 * Kept free of React/UI concerns so they can be unit-tested in isolation.
 */
import type { SnapshotHolding } from './components/types'

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
