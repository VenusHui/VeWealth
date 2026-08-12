import { describe, it, expect } from 'vitest'
import {
  parseStrategyParams,
  fmtSymbolLabel,
  normalizeEquityByDate,
  buildComparisonChartData,
  compareSnapshotHoldings,
  clampIndex,
  clampPct,
} from './calc'
import type { SnapshotHolding } from './components/types'

describe('parseStrategyParams', () => {
  it('casts numeric-looking strings to numbers', () => {
    expect(parseStrategyParams({ fast: '5', slow: '20.5', negative: '-3' })).toEqual({
      fast: 5,
      slow: 20.5,
      negative: -3,
    })
  })

  it('keeps non-numeric strings as-is', () => {
    expect(parseStrategyParams({ name: 'ma_cross', desc: 'v1' })).toEqual({
      name: 'ma_cross',
      desc: 'v1',
    })
  })

  it('returns an empty object for empty input', () => {
    expect(parseStrategyParams({})).toEqual({})
  })
})

describe('fmtSymbolLabel', () => {
  it('formats symbol with stock name', () => {
    expect(fmtSymbolLabel('600519', '贵州茅台')).toBe('贵州茅台 (600519)')
  })

  it('falls back to the code when no name', () => {
    expect(fmtSymbolLabel('600519')).toBe('600519')
    expect(fmtSymbolLabel('600519', undefined)).toBe('600519')
  })

  it('uses a dash when symbol is missing', () => {
    expect(fmtSymbolLabel(undefined, '贵州茅台')).toBe('贵州茅台 (-)')
    expect(fmtSymbolLabel()).toBe('-')
  })
})

describe('normalizeEquityByDate', () => {
  it('normalizes against the first positive equity', () => {
    const rows = [
      { trade_date: '2025-01-02', equity: 0 },
      { trade_date: '2025-01-03', equity: 100 },
      { trade_date: '2025-01-06', equity: 110 },
      { trade_date: '2025-01-07', equity: 95 },
    ]
    const map = normalizeEquityByDate(rows)
    expect(map.get('2025-01-03')).toBe(1)
    expect(map.get('2025-01-06')).toBe(1.1)
    expect(map.get('2025-01-07')).toBe(0.95)
    // Zero-equity rows are included with value 0 (normalized against the base)
    expect(map.get('2025-01-02')).toBe(0)
  })

  it('returns an empty map when no positive equity exists', () => {
    expect(normalizeEquityByDate([{ trade_date: '2025-01-02', equity: 0 }]).size).toBe(0)
    expect(normalizeEquityByDate([]).size).toBe(0)
  })
})

describe('buildComparisonChartData', () => {
  const dates = ['2025-01-03', '2025-01-06', '2025-01-07']
  const main = new Map<string, number>([
    ['2025-01-03', 1],
    ['2025-01-06', 1.1],
    ['2025-01-07', 0.95],
  ])
  const benchmark = new Map<string, number>([['2025-01-03', 1]])
  const compare = new Map<string, number>([['2025-01-07', 1.02]])

  it('joins main/benchmark/compare normalized values by date', () => {
    const rows = buildComparisonChartData(dates, main, benchmark, compare)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toEqual({ trade_date: '2025-01-03', main_norm: 1, benchmark_norm: 1, compare_norm: null })
    expect(rows[2]).toEqual({ trade_date: '2025-01-07', main_norm: 0.95, benchmark_norm: null, compare_norm: 1.02 })
  })

  it('drops dates that have no main value', () => {
    const partialMain = new Map([['2025-01-06', 1.1]])
    const rows = buildComparisonChartData(['2025-01-03', '2025-01-06'], partialMain, new Map(), new Map())
    expect(rows).toHaveLength(1)
    expect(rows[0].trade_date).toBe('2025-01-06')
  })
})

describe('compareSnapshotHoldings', () => {
  const base: SnapshotHolding = {
    symbol: '000001',
    qty: 100,
    market_value: 1000,
    position_status: 'holding',
  }

  it('sorts closed_today rows last (dimmed in the UI)', () => {
    const holding: SnapshotHolding = { ...base, symbol: 'A' }
    const closed: SnapshotHolding = { ...base, symbol: 'B', position_status: 'closed_today' }
    const rows = [holding, closed]
    rows.sort(compareSnapshotHoldings)
    expect(rows[0].symbol).toBe('A')
    expect(rows[1].symbol).toBe('B')
  })

  it('sorts by market value descending within the same status', () => {
    const small: SnapshotHolding = { ...base, symbol: 'A', market_value: 100 }
    const large: SnapshotHolding = { ...base, symbol: 'B', market_value: 500 }
    const rows = [small, large]
    rows.sort(compareSnapshotHoldings)
    expect(rows[0].symbol).toBe('B')
  })
})

describe('clampIndex', () => {
  it('clamps into [0, max]', () => {
    expect(clampIndex(-5, 10)).toBe(0)
    expect(clampIndex(3, 10)).toBe(3)
    expect(clampIndex(99, 10)).toBe(10)
  })
})

describe('clampPct', () => {
  it('clamps a progress value into [0, 100]', () => {
    expect(clampPct(45)).toBe(45)
    expect(clampPct(-10)).toBe(0)
    expect(clampPct(150)).toBe(100)
  })

  it('accepts numeric strings', () => {
    expect(clampPct('55')).toBe(55)
  })

  it('falls back for nullish values', () => {
    expect(clampPct(null)).toBe(0)
    expect(clampPct(undefined)).toBe(0)
    expect(clampPct('')).toBe(0)
  })
})
