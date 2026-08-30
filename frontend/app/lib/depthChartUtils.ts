/**
 * Data transformation helpers for lightweight-charts depth chart.
 */

// A-share color convention: red = up, green = down.
// These hex values mirror the CSS tokens `--up` / `--down` (see globals.css) so
// canvas-drawn candle colors stay in sync with the tokenized UI colors.
export const UP_COLOR = '#d92d2d'
export const DOWN_COLOR = '#0f9d58'
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
// Client-side GMM fitting on Volume Profile distribution
export function fitGaussianMixture(
  profile: VolumeProfilePoint[],
  maxComponents: number = 3,
): { n_components: number; components: Array<{ mean: number; std: number; weight: number }>; fit_curve: Array<{ price: number; fitVolume: number }> } | null {
  if (profile.length < 5) return null

  const prices = profile.map((p) => p.price)
  const volumes = profile.map((p) => p.volume)
  const maxVol = Math.max(...volumes, 1)

  // Find peaks (local maxima above noise threshold)
  const threshold = maxVol * 0.03
  const peakIndices: number[] = []
  for (let i = 1; i < volumes.length - 1; i++) {
    if (volumes[i] > threshold && volumes[i] >= volumes[i - 1] && volumes[i] > volumes[i + 1]) {
      peakIndices.push(i)
    }
  }

  if (peakIndices.length === 0) return null

  // Sort peaks by volume, take top N
  peakIndices.sort((a, b) => volumes[b] - volumes[a])
  const topPeaks = peakIndices.slice(0, maxComponents)

  // Estimate Gaussian parameters for each peak.
  // Use a wider std so the curve envelopes the bars smoothly rather
  // than creating sharp spikes at individual peaks.
  const totalPriceRange = prices[prices.length - 1] - prices[0]
  const components = topPeaks.map((idx) => {
    const mean = prices[idx]
    const peakVol = volumes[idx]
    // Find peak width at 25% height (wider than 60% → smoother envelope)
    const halfH = peakVol * 0.25
    let left = idx
    let right = idx
    while (left > 0 && volumes[left - 1] > halfH) left--
    while (right < volumes.length - 1 && volumes[right + 1] > halfH) right++
    const priceWidth = prices[right] - prices[left]
    // Use a minimum std so even narrow peaks produce visible curves
    const std = Math.max(priceWidth / 2, totalPriceRange * 0.02)

    return { mean, std, peakVol }
  })

  const totalPeakVol = components.reduce((s, c) => s + c.peakVol, 0)

  // Weight each component proportional to its peak volume
  const normalized = components.map((c) => ({
    mean: c.mean,
    std: c.std,
    weight: c.peakVol / (totalPeakVol || 1),
    peakVol: c.peakVol,
  }))

  // Generate fit curve (200 points across the price range)
  const priceMin = prices[0]
  const priceMax = prices[prices.length - 1]
  const steps = 200
  const step = (priceMax - priceMin) / (steps - 1)
  const fitCurve: Array<{ price: number; fitVolume: number }> = []

  for (let i = 0; i < steps; i++) {
    const price = priceMin + i * step
    let fitVol = 0
    for (const c of normalized) {
      const z = (price - c.mean) / c.std
      // Scale each component's contribution by its peak volume so the
      // curve height at each mean equals the bar height at that price
      fitVol += c.peakVol * Math.exp(-0.5 * z * z)
    }
    fitCurve.push({ price, fitVolume: fitVol })
  }

  return {
    n_components: normalized.length,
    components: normalized,
    fit_curve: fitCurve,
  }
}

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

// ---------------------------------------------------------------------------
// Client-side GMM peak fitting on Volume Profile
// ---------------------------------------------------------------------------

export interface GMMPeak {
  price: number
  volume: number
  width: number
  weight: number
}

export interface GMMFitResult2 {
  peaks: GMMPeak[]
  curve: Array<{ price: number; fitVolume: number }>
}

export function fitGMMToProfile(
  profile: VolumeProfilePoint[],
  maxPeaks: number = 4,
): GMMFitResult2 | null {
  if (profile.length < 5) return null

  const prices = profile.map((p) => p.price)
  const volumes = profile.map((p) => p.volume)
  const totalVol = volumes.reduce((s, v) => s + v, 0)
  if (totalVol <= 0) return null

  const kw = Math.max(3, Math.floor(profile.length / 20))
  const smoothed = new Float64Array(profile.length)
  for (let i = 0; i < profile.length; i++) {
    let s = 0, ws = 0
    for (let j = -kw; j <= kw; j++) {
      const idx = i + j
      if (idx < 0 || idx >= profile.length) continue
      const w = Math.exp(-0.5 * (j / kw) ** 2)
      s += volumes[idx] * w; ws += w
    }
    smoothed[i] = s / ws
  }

  const maxSm = smoothed.reduce((m, v) => v > m ? v : m, 0)
  const peakIndices: number[] = []
  for (let i = 1; i < profile.length - 1; i++) {
    if (smoothed[i] > smoothed[i - 1] && smoothed[i] >= smoothed[i + 1] && smoothed[i] > 0.1 * maxSm) {
      peakIndices.push(i)
    }
  }
  peakIndices.sort((a, b) => smoothed[b] - smoothed[a])
  const top = peakIndices.slice(0, maxPeaks).sort((a, b) => a - b)

  const peaks: GMMPeak[] = []
  for (const idx of top) {
    const pv = smoothed[idx]
    let l = idx, r = idx
    while (l > 0 && smoothed[l] > pv * 0.6) l--
    while (r < profile.length - 1 && smoothed[r] > pv * 0.6) r++
    const w = ((prices[r] - prices[l]) / 2.355) || (prices[1] - prices[0]) * 2
    peaks.push({ price: prices[idx], volume: volumes[idx], width: w, weight: smoothed[idx] / smoothed.reduce((s, v) => s + v, 0) })
  }

  const curve: Array<{ price: number; fitVolume: number }> = []
  for (let i = 0; i <= 200; i++) {
    const p = prices[0] + (i / 200) * (prices[prices.length - 1] - prices[0])
    let fv = 0
    for (const peak of peaks) {
      const z = (p - peak.price) / (peak.width || 0.01)
      fv += peak.volume * Math.exp(-0.5 * z * z)
    }
    curve.push({ price: Math.round(p * 100) / 100, fitVolume: Math.round(fv * 100) / 100 })
  }
  return { peaks, curve }
}
