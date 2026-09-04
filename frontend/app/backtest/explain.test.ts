import { describe, it, expect } from 'vitest'
import { analyzeFunnel } from './explain'
import type { Diagnostics } from './components/types'

const fullScan: Diagnostics = {
  universe_size: 5000,
  data_available_count: 4800,
  data_empty_count: 200,
  candidate_count: 96,
  ranked_count: 96,
  selected_count: 20,
  ordered_count: 18,
  event_count: 18,
  policy_profile: 'default',
}

describe('analyzeFunnel', () => {
  it('maps diagnostics into the 6-stage funnel', () => {
    const a = analyzeFunnel(fullScan)
    expect(a.hasDiagnostics).toBe(true)
    expect(a.stages.map((s) => s.label)).toEqual(['股票池', '数据可用', '候选', '入选', '下单', '成交'])
    expect(a.stages.map((s) => s.count)).toEqual([5000, 4800, 96, 20, 18, 18])
    expect(a.eventCount).toBe(18)
  })

  it('computes per-stage drops and data-missing reason', () => {
    const a = analyzeFunnel(fullScan)
    expect(a.stages[1].dropped).toBe(200)
    expect(a.stages[1].reason).toContain('200 只无可用数据被跳过')
    expect(a.stages[2].dropped).toBe(4704)
    expect(a.stages[2].reason).toContain('未产生候选信号')
  })

  it('returns no root cause when trades exist', () => {
    const a = analyzeFunnel(fullScan)
    expect(a.rootCause).toBeNull()
    expect(a.fixSuggestion).toBeNull()
  })

  it('flags empty pool as the root cause when universe_size is 0', () => {
    const a = analyzeFunnel({ ...fullScan, universe_size: 0, data_available_count: 0, candidate_count: 0, selected_count: 0, ordered_count: 0, event_count: 0 })
    expect(a.rootCause).toContain('股票池为空')
    expect(a.fixSuggestion).toContain('custom')
  })

  it('gives the single root cause for no-signal (candidate_count=0)', () => {
    const a = analyzeFunnel({ ...fullScan, candidate_count: 0, selected_count: 0, ordered_count: 0, event_count: 0 })
    expect(a.rootCause).toContain('候选信号')
    expect(a.fixSuggestion).toContain('放宽策略参数')
  })

  it('gives the single root cause for fully-missing data (data_available_count=0)', () => {
    const a = analyzeFunnel({ universe_size: 5000, data_available_count: 0, data_empty_count: 5000, candidate_count: 0, selected_count: 0, ordered_count: 0, event_count: 0 })
    expect(a.rootCause).toContain('行情数据缺失')
  })

  it('gives the single root cause for execution failure (ordered>0, event=0)', () => {
    const a = analyzeFunnel({ ...fullScan, ordered_count: 5, event_count: 0 })
    expect(a.rootCause).toContain('未能成交')
  })

  it('reports degraded provenance', () => {
    const a = analyzeFunnel({ ...fullScan, data_provenance: { gap_count: 3, failure_count: 1, degraded: true } })
    expect(a.degraded).toBe(true)
  })

  it('returns no diagnostics info for a blank input (manual_symbols mode)', () => {
    const a = analyzeFunnel(null)
    expect(a.hasDiagnostics).toBe(false)
    expect(a.stages).toEqual([])
    expect(a.rootCause).toBeNull()
  })

  it('handles missing numeric fields gracefully (treated as 0)', () => {
    const a = analyzeFunnel({ universe_size: 100, data_available_count: 80 } as Diagnostics)
    expect(a.hasDiagnostics).toBe(true)
    expect(a.stages[2].count).toBe(0)
    expect(a.stages[2].dropped).toBe(80)
  })
})
