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
  bin_size: number
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
  time: number
  open: number
  high: number
  low: number
  close: number
}

function toUnixTime(datetime: string): number {
  return Math.floor(new Date(datetime.replace(' ', 'T') + '+08:00').getTime() / 1000)
}

export function ohlcvToCandlestickData(klines: KlineDataPoint[]): CandlestickData[] {
  return klines.map((k) => ({
    time: toUnixTime(k.datetime),
    open: k.open,
    high: k.high,
    low: k.low,
    close: k.close,
  }))
}

export function computeMALine(klines: KlineDataPoint[], period: number): { time: number; value: number }[] {
  if (klines.length < period) return []
  const result: { time: number; value: number }[] = []
  let sum = 0
  for (let i = 0; i < period - 1; i++) {
    sum += klines[i].close
  }
  for (let i = period - 1; i < klines.length; i++) {
    sum += klines[i].close
    result.push({ time: toUnixTime(klines[i].datetime), value: sum / period })
    sum -= klines[i - period + 1].close
  }
  return result
}

export function computeVWAPLine(klines: KlineDataPoint[]): { time: number; value: number }[] {
  let cumVol = 0
  let cumVP = 0
  const result: { time: number; value: number }[] = []
  for (const k of klines) {
    const typical = (k.high + k.low + k.close) / 3
    cumVol += k.volume
    cumVP += typical * k.volume
    result.push({ time: toUnixTime(k.datetime), value: cumVol > 0 ? cumVP / cumVol : k.close })
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

// Client-side Volume Profile computation (visible range only)
export function computeVolumeProfileFromKlines(
  klines: KlineDataPoint[],
  bins: number = 80,
): VolumeProfileData | null {
  if (klines.length < 2) return null

  let priceMin = Infinity
  let priceMax = -Infinity
  let totalVolume = 0
  let vwapNum = 0
  let vwapDen = 0

  for (const k of klines) {
    if (k.low < priceMin) priceMin = k.low
    if (k.high > priceMax) priceMax = k.high
    totalVolume += k.volume
    const typical = (k.high + k.low + k.close) / 3
    vwapNum += typical * k.volume
    vwapDen += k.volume
  }

  if (priceMax <= priceMin) priceMax = priceMin + 0.01
  const binSize = (priceMax - priceMin) / bins
  const profile = new Float64Array(bins)

  for (const k of klines) {
    const vol = k.volume
    const barRange = k.high - k.low
    if (vol <= 0) continue

    if (barRange <= 0) {
      const b = Math.max(0, Math.min(bins - 1, Math.floor((k.low - priceMin) / binSize)))
      profile[b] += vol
    } else {
      const volPerUnit = vol / barRange
      const lowB = Math.max(0, Math.min(bins - 1, Math.floor((k.low - priceMin) / binSize)))
      const highB = Math.max(0, Math.min(bins - 1, Math.floor((k.high - priceMin) / binSize)))
      if (lowB === highB) {
        profile[lowB] += vol
      } else {
        for (let b = lowB; b <= highB; b++) {
          const binLow = priceMin + b * binSize
          const binHigh = binLow + binSize
          const overlap = Math.max(0, Math.min(k.high, binHigh) - Math.max(k.low, binLow))
          profile[b] += volPerUnit * overlap
        }
      }
    }
  }

  const profileList: VolumeProfilePoint[] = []
  for (let i = 0; i < bins; i++) {
    profileList.push({
      price: Math.round((priceMin + (i + 0.5) * binSize) * 1000) / 1000,
      volume: Math.round(profile[i] * 100) / 100,
    })
  }

  // POC
  let pocIdx = 0
  for (let i = 1; i < bins; i++) {
    if (profile[i] > profile[pocIdx]) pocIdx = i
  }

  // Value Area
  if (totalVolume <= 0) {
    return {
      total_volume: 0,
      price_min: Math.round(priceMin * 1000) / 1000,
      price_max: Math.round(priceMax * 1000) / 1000,
      bin_size: Math.round(binSize * 10000) / 10000,
      profile: profileList,
      poc: { price: 0, volume: 0 },
      value_area: { vah: 0, val: 0, volume_pct: 0 },
      hvn_levels: [],
      lvn_levels: [],
      vwap: 0,
    }
  }

  const targetVol = totalVolume * 0.7
  let accVol = profile[pocIdx]
  let lowIdx = pocIdx
  let highIdx = pocIdx
  while (accVol < targetVol) {
    const canLow = lowIdx > 0
    const canHigh = highIdx < bins - 1
    if (!canLow && !canHigh) break
    if (canLow && canHigh) {
      if (profile[lowIdx - 1] >= profile[highIdx + 1]) { lowIdx--; accVol += profile[lowIdx] }
      else { highIdx++; accVol += profile[highIdx] }
    } else if (canLow) { lowIdx--; accVol += profile[lowIdx] }
    else { highIdx++; accVol += profile[highIdx] }
  }

  // HVN/LVN
  const meanVol = totalVolume / bins
  const variance = profile.reduce((s, v) => s + (v - meanVol) ** 2, 0) / bins
  const stdVol = Math.sqrt(variance)
  const hvnLevels: number[] = []
  const lvnLevels: number[] = []
  if (stdVol > 0) {
    for (let i = 0; i < bins; i++) {
      const price = priceMin + (i + 0.5) * binSize
      if (profile[i] > meanVol + 1.5 * stdVol) hvnLevels.push(Math.round(price * 1000) / 1000)
      else if (profile[i] < meanVol - 0.5 * stdVol) lvnLevels.push(Math.round(price * 1000) / 1000)
    }
  }

  return {
    total_volume: Math.round(totalVolume * 100) / 100,
    price_min: Math.round(priceMin * 1000) / 1000,
    price_max: Math.round(priceMax * 1000) / 1000,
    bin_size: Math.round(binSize * 10000) / 10000,
    profile: profileList,
    poc: {
      price: Math.round((priceMin + (pocIdx + 0.5) * binSize) * 1000) / 1000,
      volume: Math.round(profile[pocIdx] * 100) / 100,
    },
    value_area: {
      vah: Math.round((priceMin + (highIdx + 0.5) * binSize) * 1000) / 1000,
      val: Math.round((priceMin + (lowIdx + 0.5) * binSize) * 1000) / 1000,
      volume_pct: Math.round((accVol / totalVolume) * 1000) / 10,
    },
    hvn_levels: hvnLevels,
    lvn_levels: lvnLevels,
    vwap: Math.round((vwapDen > 0 ? vwapNum / vwapDen : 0) * 1000) / 1000,
  }
}
