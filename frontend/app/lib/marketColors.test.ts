import { describe, it, expect } from 'vitest'
import {
  marketClassByValue,
  marketTagColorByValue,
  formatPct,
  marketClassByDrawdown,
  formatDrawdownPct,
} from './marketColors'

describe('marketClassByValue (A股: 红涨绿跌)', () => {
  it('returns red for positive values', () => {
    expect(marketClassByValue(0.01)).toBe('text-red-600')
    expect(marketClassByValue('2.5')).toBe('text-red-600')
  })

  it('returns green for negative values', () => {
    expect(marketClassByValue(-0.01)).toBe('text-green-600')
  })

  it('returns gray for zero and non-numeric values', () => {
    expect(marketClassByValue(0)).toBe('text-gray-500')
    expect(marketClassByValue('-')).toBe('text-gray-500')
    expect(marketClassByValue(null)).toBe('text-gray-500')
    expect(marketClassByValue(NaN)).toBe('text-gray-500')
  })
})

describe('marketTagColorByValue', () => {
  it('maps sign to antd tag colors', () => {
    expect(marketTagColorByValue(0.01)).toBe('red')
    expect(marketTagColorByValue(-0.01)).toBe('green')
    expect(marketTagColorByValue(0)).toBe('default')
    expect(marketTagColorByValue('abc')).toBe('default')
  })
})

describe('formatPct', () => {
  it('formats a ratio as a percentage string', () => {
    expect(formatPct(0.1234)).toBe('12.34%')
    expect(formatPct(1)).toBe('100.00%')
    expect(formatPct(-0.5)).toBe('-50.00%')
  })

  it('honors the digits parameter', () => {
    expect(formatPct(0.1234, 0)).toBe('12%')
    expect(formatPct(0.1234, 3)).toBe('12.340%')
  })

  it('returns "-" for non-numeric input', () => {
    expect(formatPct('n/a')).toBe('-')
    expect(formatPct(undefined)).toBe('-')
    expect(formatPct(NaN)).toBe('-')
  })

  it('treats null/empty string as 0 (Number coercion)', () => {
    expect(formatPct(null)).toBe('0.00%')
    expect(formatPct('')).toBe('0.00%')
  })
})

describe('marketClassByDrawdown', () => {
  it('renders drawdown as green (A股 回撤为绿)', () => {
    expect(marketClassByDrawdown(-0.1)).toBe('text-green-600')
    expect(marketClassByDrawdown(0.1)).toBe('text-green-600')
  })

  it('returns gray for zero and non-numeric input', () => {
    expect(marketClassByDrawdown(0)).toBe('text-gray-500')
    expect(marketClassByDrawdown('-')).toBe('text-gray-500')
    expect(marketClassByDrawdown(NaN)).toBe('text-gray-500')
  })
})

describe('formatDrawdownPct', () => {
  it('formats a drawdown as an absolute negative percentage', () => {
    expect(formatDrawdownPct(-0.123)).toBe('-12.30%')
    expect(formatDrawdownPct(0.123)).toBe('-12.30%')
  })

  it('returns "-" for non-numeric input', () => {
    expect(formatDrawdownPct('x')).toBe('-')
    expect(formatDrawdownPct(undefined)).toBe('-')
    expect(formatDrawdownPct(NaN)).toBe('-')
  })

  it('treats null as 0 (Number coercion)', () => {
    expect(formatDrawdownPct(null)).toBe('-0.00%')
  })
})
