import { describe, it, expect } from 'vitest'
import {
  parseStrategyParams,
  fmtSymbolLabel,
  normalizeEquityByDate,
  buildComparisonChartData,
  compareSnapshotHoldings,
  clampIndex,
  clampPct,
  defaultDateRange,
  computeStockCount,
  estimateScanDuration,
  buildStrategyParams,
  buildCostSummary,
  countTradingDays,
  checkDataFreshness,
  classifyParamGroup,
  resolveParamMeta,
} from './calc'
import type { SnapshotHolding, Strategy } from './components/types'
import { DEFAULT_COST_CONFIG } from './components/types'

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

// ---------------------------------------------------------------------------
// Shared schema-driven strategy config helpers (VEW-28)
// ---------------------------------------------------------------------------

const MA_STRATEGY: Strategy = {
  strategy_id: 'ma_cross_v1',
  name: '双均线策略 v1',
  description: '',
  param_schema: [
    { key: 'short_window', label: '短均线周期', type: 'int', default: 5, min: 2, max: 120 },
    { key: 'long_window', label: '长均线周期', type: 'int', default: 20, min: 3, max: 240 },
    { key: 'hold_days', label: '持有天数', type: 'int', default: 5, min: 1, max: 60 },
    { key: 'position_size_pct', label: '单笔仓位占比', type: 'float', default: 0.1, min: 0.01, max: 1.0 },
  ],
  usable: true,
  unusable_reasons: [],
  supported_modes: ['manual_symbols', 'strategy_select'],
  min_history_bars: 240,
}

describe('defaultDateRange', () => {
  it('returns a recent complete trading range ending on a weekday', () => {
    const r = defaultDateRange(new Date(2025, 5, 10)) // 2025-06-10 Tue
    expect(r.endDate).toBe('2025-06-10')
    expect(r.startDate).toBe('2024-06-10')
  })

  it('pushes a weekend reference back to the previous Friday', () => {
    const r = defaultDateRange(new Date(2025, 5, 15)) // 2025-06-15 Sun
    expect(r.endDate).toBe('2025-06-13') // Friday
  })

  it('sets a start roughly one year before the end', () => {
    const r = defaultDateRange(new Date(2025, 5, 10))
    expect(countTradingDays(r.startDate, r.endDate)).toBeGreaterThan(240)
  })
})

describe('computeStockCount', () => {
  const stats = {
    total_active: 180,
    st_active: 18,
    non_st_active: 162,
    by_board: { main: 100, gem: 50, star: 20, bse: 10 },
    by_board_exclude_st: { main: 90, gem: 45, star: 18, bse: 9 },
    defaults: { boards: ['main'], exclude_st: true },
  } as const

  it('sums the selected boards with ST excluded when enabled', () => {
    expect(computeStockCount(stats as never, ['main', 'gem'], true)).toBe(135)
  })

  it('includes ST when exclude_st is false', () => {
    expect(computeStockCount(stats as never, ['main', 'gem'], false)).toBe(150)
  })

  it('returns 0 for empty stats or boards', () => {
    expect(computeStockCount(undefined, ['main'], true)).toBe(0)
    expect(computeStockCount(stats as never, [], true)).toBe(0)
  })
})

describe('estimateScanDuration', () => {
  it('returns 0 for no stocks', () => {
    expect(estimateScanDuration(0)).toBe(0)
  })

  it('is at least a small floor duration', () => {
    expect(estimateScanDuration(100)).toBe(5)
  })

  it('scales up with count', () => {
    expect(estimateScanDuration(5000)).toBe(150)
  })
})

describe('buildStrategyParams', () => {
  const raw = { short_window: '5', long_window: '20', hold_days: '5', position_size_pct: '0.1' }

  it('casts numeric schema params and keeps execution params in backtest', () => {
    expect(buildStrategyParams(MA_STRATEGY, raw, 'strategy_select')).toEqual({
      short_window: 5,
      long_window: 20,
      hold_days: 5,
      position_size_pct: 0.1,
    })
  })

  it('drops execution-only params in screener mode', () => {
    expect(buildStrategyParams(MA_STRATEGY, raw, 'screener')).toEqual({
      short_window: 5,
      long_window: 20,
    })
  })

  it('keeps numeric-looking unknown params and drops empty numerics', () => {
    expect(buildStrategyParams(MA_STRATEGY, { foo: '3', bar: 'abc' }, 'strategy_select')).toEqual({ foo: 3, bar: 'abc' })
    expect(buildStrategyParams(MA_STRATEGY, { short_window: '' }, 'strategy_select')).toEqual({})
  })

  it('produces identical signal params across both entry points', () => {
    const backtest = buildStrategyParams(MA_STRATEGY, raw, 'strategy_select')
    const screener = buildStrategyParams(MA_STRATEGY, raw, 'screener')
    // screener params are exactly the signal portion of backtest params (no drift)
    expect(screener).toEqual({ short_window: 5, long_window: 20 })
    expect(backtest).toMatchObject(screener)
  })
})

describe('classifyParamGroup / resolveParamMeta', () => {
  it('groups known signal/portfolio keys', () => {
    expect(classifyParamGroup('short_window')).toBe('signal')
    expect(classifyParamGroup('position_size_pct')).toBe('portfolio')
    expect(classifyParamGroup('unknown')).toBe('signal')
  })

  it('prefers backend-provided group over the frontend fallback', () => {
    const meta = resolveParamMeta('short_window', { key: 'short_window', label: 'x', type: 'int', group: 'portfolio' })
    expect(meta.group).toBe('portfolio')
  })

  it('falls back to frontend metadata for unit/help', () => {
    expect(resolveParamMeta('position_size_pct').unit).toBe('0~1')
    expect(resolveParamMeta('short_window').help).toContain('短均线')
  })
})

describe('countTradingDays', () => {
  it('counts weekdays only', () => {
    expect(countTradingDays('2025-01-01', '2025-01-07')).toBe(5) // Wed–Fri + Mon–Tue
    expect(countTradingDays('2025-01-04', '2025-01-05')).toBe(0) // weekend
  })

  it('returns 0 for an inverted range', () => {
    expect(countTradingDays('2025-01-05', '2025-01-03')).toBe(0)
  })
})

describe('checkDataFreshness', () => {
  it('is satisfied when the range covers enough bars and ends in the past', () => {
    // reference 定在 12 月底，使得 6 月底结束日在「最近完整交易日」之前。
    const f = checkDataFreshness('2025-01-01', '2025-06-30', 120, new Date(2025, 11, 30))
    expect(f.enoughBars).toBe(true)
    expect(f.endInFuture).toBe(false)
  })

  it('flags insufficient bars and future end dates', () => {
    // reference 为 2025-01-10（周五），结束日 01-20 晚于最近完整交易日 → 视为未来。
    const f = checkDataFreshness('2025-01-01', '2025-01-20', 240, new Date(2025, 0, 10))
    expect(f.enoughBars).toBe(false)
    expect(f.endInFuture).toBe(true)
  })
})

describe('buildCostSummary', () => {
  it('formats commission, stamp tax and slippage', () => {
    expect(buildCostSummary(DEFAULT_COST_CONFIG)).toContain('佣金 0.030%')
    expect(buildCostSummary(DEFAULT_COST_CONFIG)).toContain('最低 5.00 元')
    expect(buildCostSummary(DEFAULT_COST_CONFIG)).toContain('印花税 0.100%')
    expect(buildCostSummary(DEFAULT_COST_CONFIG)).toContain('滑点 0.050%')
  })
})
