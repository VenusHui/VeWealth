import { describe, it, expect } from 'vitest'
import {
  ohlcvToCandlestickData,
  computeMALine,
  computeVWAPLine,
  getMaxProfileVolume,
  fitGaussianMixture,
  computeVolumeProfileFromKlines,
  fitGMMToProfile,
  type KlineDataPoint,
  type VolumeProfilePoint,
} from './depthChartUtils'

function makeKline(
  datetime: string,
  open: number,
  close: number,
  high: number,
  low: number,
  volume: number,
): KlineDataPoint {
  return { datetime, open, close, high, low, volume }
}

describe('ohlcvToCandlestickData', () => {
  it('converts a kline to lightweight-charts candlestick with Beijing time (UTC+8)', () => {
    const klines = [makeKline('2024-01-01 09:30:00', 10, 10.5, 11, 9.5, 100)]
    const result = ohlcvToCandlestickData(klines)
    expect(result).toHaveLength(1)
    // 2024-01-01T09:30:00+08:00 == 2024-01-01T01:30:00Z == 1704072600
    expect(result[0]).toEqual({
      time: 1704072600,
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
    })
  })

  it('returns an empty array for empty input', () => {
    expect(ohlcvToCandlestickData([])).toEqual([])
  })

  it('handles dates without a space separator', () => {
    const klines = [makeKline('2024-01-01T09:30:00', 1, 2, 3, 1, 10)]
    const result = ohlcvToCandlestickData(klines)
    expect(result[0].time).toBe(1704072600)
  })
})

describe('computeMALine', () => {
  const klines: KlineDataPoint[] = [
    makeKline('2024-01-01 09:30:00', 10, 10, 10, 10, 100),
    makeKline('2024-01-02 09:30:00', 11, 11, 11, 11, 100),
    makeKline('2024-01-03 09:30:00', 12, 12, 12, 12, 100),
    makeKline('2024-01-04 09:30:00', 13, 13, 13, 13, 100),
    makeKline('2024-01-05 09:30:00', 14, 14, 14, 14, 100),
  ]

  it('computes a simple moving average of the given period', () => {
    const result = computeMALine(klines, 3)
    expect(result).toHaveLength(3)
    expect(result.map((p) => p.value)).toEqual([11, 12, 13])
  })

  it('returns an empty array when there are fewer bars than the period', () => {
    expect(computeMALine(klines, 10)).toEqual([])
    expect(computeMALine([], 3)).toEqual([])
  })

  it('emits values at the bar timestamps', () => {
    const result = computeMALine(klines, 1)
    expect(result).toHaveLength(5)
    expect(result[0].value).toBe(10)
  })
})

describe('computeVWAPLine', () => {
  it('computes cumulative volume-weighted average price', () => {
    const klines: KlineDataPoint[] = [
      makeKline('2024-01-01 09:30:00', 12, 11, 12, 10, 100), // typical 11
      makeKline('2024-01-02 09:30:00', 13, 12, 13, 11, 100), // typical 12
      makeKline('2024-01-03 09:30:00', 14, 13, 14, 12, 200), // typical 13
    ]
    const result = computeVWAPLine(klines)
    expect(result).toHaveLength(3)
    expect(result[0].value).toBe(11)
    expect(result[1].value).toBe(11.5) // (11*100 + 12*100) / 200
    expect(result[2].value).toBe(12.25) // (2300 + 13*200) / 400
  })

  it('falls back to close price when cumulative volume is zero', () => {
    const klines: KlineDataPoint[] = [
      makeKline('2024-01-01 09:30:00', 12, 11, 12, 10, 0),
      makeKline('2024-01-02 09:30:00', 13, 12, 13, 11, 100),
    ]
    const result = computeVWAPLine(klines)
    expect(result[0].value).toBe(11)
    expect(result[1].value).toBe(12)
  })
})

describe('getMaxProfileVolume', () => {
  it('returns 0 for an empty profile', () => {
    expect(getMaxProfileVolume([])).toBe(0)
  })

  it('returns the maximum bar volume', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 5 },
      { price: 11, volume: 9 },
      { price: 12, volume: 2 },
    ]
    expect(getMaxProfileVolume(profile)).toBe(9)
  })
})

describe('fitGaussianMixture', () => {
  it('returns null when the profile has fewer than 5 bars', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 2 },
      { price: 12, volume: 3 },
    ]
    expect(fitGaussianMixture(profile)).toBeNull()
  })

  it('returns null when no peaks are found', () => {
    // Monotone profile — no local maxima
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 2 },
      { price: 12, volume: 3 },
      { price: 13, volume: 4 },
      { price: 14, volume: 5 },
    ]
    expect(fitGaussianMixture(profile)).toBeNull()
  })

  it('fits a single component on a single-peak profile', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 2 },
      { price: 12, volume: 5 },
      { price: 13, volume: 2 },
      { price: 14, volume: 1 },
    ]
    const result = fitGaussianMixture(profile)
    expect(result).not.toBeNull()
    expect(result!.n_components).toBe(1)
    expect(result!.components[0].mean).toBe(12)
    expect(result!.components[0].weight).toBeCloseTo(1, 6)
    // Fit curve spans the price range with 200 points
    expect(result!.fit_curve).toHaveLength(200)
    expect(result!.fit_curve[0].price).toBe(10)
    expect(result!.fit_curve[199].price).toBe(14)
  })

  it('caps components at maxComponents and weights sum to 1', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 5 },
      { price: 12, volume: 2 },
      { price: 13, volume: 1 },
      { price: 14, volume: 2 },
      { price: 15, volume: 6 },
      { price: 16, volume: 1 },
    ]
    const result = fitGaussianMixture(profile, 2)
    expect(result).not.toBeNull()
    expect(result!.n_components).toBe(2)
    const weightSum = result!.components.reduce((s, c) => s + c.weight, 0)
    expect(weightSum).toBeCloseTo(1, 6)
    // Peaks sorted by volume desc — the 6-volume peak should win
    expect(result!.components[0].mean).toBe(15)
  })
})

describe('computeVolumeProfileFromKlines', () => {
  it('returns null with fewer than 2 klines', () => {
    expect(computeVolumeProfileFromKlines([], 80)).toBeNull()
    expect(computeVolumeProfileFromKlines([makeKline('2024-01-01 09:30:00', 10, 10, 10, 10, 100)], 80)).toBeNull()
  })

  it('builds a volume profile with POC, value area and VWAP', () => {
    const klines: KlineDataPoint[] = [
      makeKline('2024-01-01 09:30:00', 10, 10, 10, 10, 100),
      makeKline('2024-01-02 09:30:00', 12, 12, 12, 12, 50),
    ]
    const vp = computeVolumeProfileFromKlines(klines, 80)
    expect(vp).not.toBeNull()
    expect(vp!.total_volume).toBe(150)
    expect(vp!.price_min).toBe(10)
    expect(vp!.price_max).toBe(12)
    expect(vp!.bin_size).toBeCloseTo(0.025, 4)
    expect(vp!.profile).toHaveLength(80)
    expect(vp!.profile[0].volume).toBe(100)
    expect(vp!.profile[79].volume).toBe(50)
    // POC is the bin with the most volume
    expect(vp!.poc.volume).toBe(100)
    expect(vp!.poc.price).toBeCloseTo(10 + 0.5 * vp!.bin_size, 2)
    // VWAP = (10*100 + 12*50) / 150
    expect(vp!.vwap).toBeCloseTo(10.667, 3)
    // Value area captures ~70% of volume
    expect(vp!.value_area.volume_pct).toBeGreaterThan(0)
    expect(vp!.value_area.volume_pct).toBeLessThanOrEqual(100)
    expect(vp!.value_area.vah).toBeGreaterThanOrEqual(vp!.value_area.val)
    // All profile volumes are non-negative
    expect(vp!.profile.every((p) => p.volume >= 0)).toBe(true)
  })

  it('handles bars with a range by distributing volume across bins', () => {
    const klines: KlineDataPoint[] = [
      makeKline('2024-01-01 09:30:00', 10, 10, 12, 10, 100), // 10..12
      makeKline('2024-01-02 09:30:00', 12, 12, 12, 12, 50),
    ]
    const vp = computeVolumeProfileFromKlines(klines, 80)
    expect(vp).not.toBeNull()
    expect(vp!.total_volume).toBe(150)
    // Volume spread across more than one bin
    const nonZero = vp!.profile.filter((p) => p.volume > 0)
    expect(nonZero.length).toBeGreaterThan(1)
    const sum = vp!.profile.reduce((s, p) => s + p.volume, 0)
    expect(sum).toBeCloseTo(150, 1)
  })

  it('returns zeroed stats when total volume is 0', () => {
    const klines: KlineDataPoint[] = [
      makeKline('2024-01-01 09:30:00', 10, 10, 10, 10, 0),
      makeKline('2024-01-02 09:30:00', 12, 12, 12, 12, 0),
    ]
    const vp = computeVolumeProfileFromKlines(klines, 80)
    expect(vp).not.toBeNull()
    expect(vp!.total_volume).toBe(0)
    expect(vp!.poc.price).toBe(0)
    expect(vp!.vwap).toBe(0)
    expect(vp!.value_area.volume_pct).toBe(0)
    expect(vp!.hvn_levels).toEqual([])
    expect(vp!.lvn_levels).toEqual([])
  })
})

describe('fitGMMToProfile', () => {
  it('returns null for profiles with fewer than 5 bars', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 2 },
      { price: 12, volume: 3 },
    ]
    expect(fitGMMToProfile(profile)).toBeNull()
  })

  it('returns null when total volume is zero', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 0 },
      { price: 11, volume: 0 },
      { price: 12, volume: 0 },
      { price: 13, volume: 0 },
      { price: 14, volume: 0 },
    ]
    expect(fitGMMToProfile(profile)).toBeNull()
  })

  it('detects peaks and generates a fit curve', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 2 },
      { price: 12, volume: 5 },
      { price: 13, volume: 2 },
      { price: 14, volume: 1 },
    ]
    const result = fitGMMToProfile(profile)
    expect(result).not.toBeNull()
    expect(result!.peaks.length).toBeGreaterThanOrEqual(1)
    const peak = result!.peaks[0]
    expect(peak.price).toBe(12)
    expect(peak.volume).toBeGreaterThan(0)
    expect(peak.width).toBeGreaterThan(0)
    expect(peak.weight).toBeGreaterThan(0)
    expect(peak.weight).toBeLessThanOrEqual(1)
    // Curve has 201 points across the price range, in ascending order
    expect(result!.curve).toHaveLength(201)
    expect(result!.curve[0].price).toBeLessThan(result!.curve[result!.curve.length - 1].price)
  })

  it('respects maxPeaks limit', () => {
    const profile: VolumeProfilePoint[] = [
      { price: 10, volume: 1 },
      { price: 11, volume: 5 },
      { price: 12, volume: 2 },
      { price: 13, volume: 1 },
      { price: 14, volume: 2 },
      { price: 15, volume: 6 },
      { price: 16, volume: 1 },
    ]
    const result = fitGMMToProfile(profile, 1)
    expect(result).not.toBeNull()
    expect(result!.peaks).toHaveLength(1)
  })
})
