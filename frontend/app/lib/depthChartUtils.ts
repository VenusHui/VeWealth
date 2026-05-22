/**
 * Data transformation helpers for lightweight-charts depth chart.
 */

// A-share color convention: red = up, green = down
export const UP_COLOR = '#dc2626'
export const DOWN_COLOR = '#16a34a'
export const UP_BORDER = '#b91c1c'
export const DOWN_BORDER = '#15803d'

export interface KlineDataPoint {
  datetime: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

export interface VolumeProfilePoint {
  price: number
  volume: number
}

export interface GMMComponent {
  mean: number
  std: number
  weight: number
  volume: number
}

export interface GMMFitCurvePoint {
  price: number
  fitVolume: number
}

export interface GMMFitResult {
  n_components: number
  components: GMMComponent[]
  fit_curve: GMMFitCurvePoint[]
  bic: number
}

export interface VolumeProfileData {
  total_volume: number
  price_min: number
  price_max: number
  profile: VolumeProfilePoint[]
  poc: { price: number; volume: number }
  value_area: { vah: number; val: number; volume_pct: number }
  hvn_levels: number[]
  lvn_levels: number[]
  vwap: number
  fit_result?: GMMFitResult | null
}

export interface CyqInfo {
  date: string
  profit_ratio: number
  avg_cost: number
  cost_90_low: number
  cost_90_high: number
  concentration_90: number
  cost_70_low: number
  cost_70_high: number
  concentration_70: number
}

interface CandlestickData {
  time: string
  open: number
  high: number
  low: number
  close: number
}

interface VolumeBarData {
  time: string
  value: number
  color: string
}

export function ohlcvToCandlestickData(klines: KlineDataPoint[]): CandlestickData[] {
  return klines.map((k) => ({
    time: k.datetime,
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
  }))
}

export function ohlcvToVolumeData(klines: KlineDataPoint[]): VolumeBarData[] {
  return klines.map((k) => ({
    time: k.datetime,
    value: k.volume,
    color: k.close >= k.open ? UP_COLOR : DOWN_COLOR,
  }))
}

export function computeMALine(klines: KlineDataPoint[], period: number): { time: string; value: number }[] {
  if (klines.length < period) return []
  const result: { time: string; value: number }[] = []
  let sum = 0
  for (let i = 0; i < period - 1; i++) {
    sum += klines[i].close
  }
  for (let i = period - 1; i < klines.length; i++) {
    sum += klines[i].close
    result.push({ time: klines[i].datetime, value: sum / period })
    sum -= klines[i - period + 1].close
  }
  return result
}

export function computeVWAPLine(klines: KlineDataPoint[]): { time: string; value: number }[] {
  let cumVol = 0
  let cumVP = 0
  const result: { time: string; value: number }[] = []
  for (const k of klines) {
    const typical = (k.high + k.low + k.close) / 3
    cumVol += k.volume
    cumVP += typical * k.volume
    result.push({ time: k.datetime, value: cumVol > 0 ? cumVP / cumVol : k.close })
  }
  return result
}

export function getMaxProfileVolume(profile: VolumeProfilePoint[]): number {
  if (!profile.length) return 0
  let max = 0
  for (const p of profile) {
    if (p.volume > max) max = p.volume
  }
  return max
}
