'use client'

import { useEffect, useRef, useCallback, useMemo, useState } from 'react'
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type LineData,
  type Time,
} from 'lightweight-charts'
import {
  type KlineDataPoint,
  type VolumeProfileData,
  type CyqInfo,
  UP_COLOR,
  DOWN_COLOR,
  UP_BORDER,
  DOWN_BORDER,
  ohlcvToCandlestickData,
  computeMALine,
  computeVWAPLine,
  getMaxProfileVolume,
  computeVolumeProfileFromKlines,
  fitGMMToProfile,
} from '../lib/depthChartUtils'

interface DepthChartProps {
  klines: KlineDataPoint[]
  volumeProfile: VolumeProfileData | null
  cyqInfo: CyqInfo | null
  showMA: boolean
  showVWAP: boolean
  showGMM: boolean
  showCYQ: boolean
  hasMore?: boolean
  loadingMore?: boolean
  onLoadMore?: () => void
  gmmThreshold?: number
  currentPrice?: number | null
}

const MA_COLORS = ['#f59e0b', '#f97316', '#8b5cf6']
const VWAP_COLOR = '#f97316'

export default function DepthChart({
  klines,
  volumeProfile,
  cyqInfo,
  showMA,
  showVWAP,
  showGMM,
  showCYQ,
  hasMore,
  loadingMore,
  onLoadMore,
  gmmThreshold = 0.7,
  currentPrice,
}: DepthChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const maSeriesRefs = useRef<ISeriesApi<'Line'>[]>([])
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const cyqPriceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([])

  const candlestickData = useMemo(() => ohlcvToCandlestickData(klines), [klines])

  // Create chart once on mount
  useEffect(() => {
    const container = chartContainerRef.current
    if (!container) return

    container.innerHTML = ''

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { color: 'transparent' },
        textColor: '#64748b',
      },
      grid: {
        vertLines: { color: 'rgba(226,232,240,0.5)' },
        horzLines: { color: 'rgba(226,232,240,0.5)' },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: '#94a3b8', style: 2, labelVisible: true },
        horzLine: { color: '#94a3b8', style: 2, labelVisible: true },
      },
      rightPriceScale: {
        borderColor: 'rgba(226,232,240,0.8)',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: 'rgba(226,232,240,0.8)',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          // Timestamps are Unix seconds. Display as Beijing time (UTC+8).
          const d = new Date(time * 1000)
          const bjHour = String((d.getUTCHours() + 8) % 24).padStart(2, '0')
          const bjMin = String(d.getUTCMinutes()).padStart(2, '0')
          const month = String(d.getUTCMonth() + 1).padStart(2, '0')
          const day = String(d.getUTCDate()).padStart(2, '0')
          return `${month}-${day} ${bjHour}:${bjMin}`
        },
      },
      localization: {
        dateFormat: 'MM-dd',
        timeFormatter: (time: number) => {
          const d = new Date(time * 1000)
          const h = String((d.getUTCHours() + 8) % 24).padStart(2, '0')
          const m = String(d.getUTCMinutes()).padStart(2, '0')
          return `${h}:${m}`
        },
      },
    })

    const cds = chart.addSeries(CandlestickSeries, {
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderUpColor: UP_BORDER,
      borderDownColor: DOWN_BORDER,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
    })

    chartRef.current = chart
    candlestickSeriesRef.current = cds

    return () => {
      chart.remove()
      chartRef.current = null
      candlestickSeriesRef.current = null
    }
  }, []) // created once, never recreated

  // Update candlestick data — proven working pattern: setData + fitContent
  useEffect(() => {
    const cds = candlestickSeriesRef.current
    const ch = chartRef.current
    if (!cds || !ch || candlestickData.length === 0) return
    cds.setData(candlestickData as CandlestickData<Time>[])
    ch.timeScale().fitContent()
  }, [candlestickData])

  // Track visible range and compute Volume Profile from visible candles only.
  // We use refs for hasMore/loadingMore/onLoadMore so the subscription handler
  // always reads the latest values without re-subscribing on every state change.
  const hasMoreRef = useRef(hasMore)
  hasMoreRef.current = hasMore
  const loadingMoreRef = useRef(loadingMore)
  loadingMoreRef.current = loadingMore
  const onLoadMoreRef = useRef(onLoadMore)
  onLoadMoreRef.current = onLoadMore

  const [visibleVP, setVisibleVP] = useState<VolumeProfileData | null>(null)

  // Use visible-range VP if available, otherwise fall back to backend VP
  const activeVP = visibleVP || volumeProfile

  // Track chart's visible price range (updates on zoom/scroll/resize)
  const [visiblePriceRange, setVisiblePriceRange] = useState<{ high: number; low: number } | null>(null)

  const updateVisiblePriceRange = useCallback(() => {
    const cds = candlestickSeriesRef.current
    const container = chartContainerRef.current
    if (!cds || !container) return
    const h = container.clientHeight
    if (h <= 0) return
    const top = cds.coordinateToPrice(0) as number | null
    const bot = cds.coordinateToPrice(h) as number | null
    if (top != null && bot != null && top > bot) {
      setVisiblePriceRange({ high: top, low: bot })
    }
  }, [])

  useEffect(() => {
    const ch = chartRef.current
    if (!ch) return

    function handleRangeChange(isInitial = false) {
      const range = ch!.timeScale().getVisibleLogicalRange()
      if (!range || klines.length === 0) {
        setVisibleVP(null)
        return
      }
      const from = Math.max(0, Math.floor(range.from))
      const to = Math.min(klines.length - 1, Math.ceil(range.to))
      const visibleKlines = klines.slice(from, to + 1)
      if (visibleKlines.length < 2) {
        setVisibleVP(null)
        return
      }
      const vp = computeVolumeProfileFromKlines(visibleKlines, 80)
      setVisibleVP(vp)

      // Only trigger loadMore from user-initiated scroll/zoom events,
      // not from programmatic fitContent (initial call after data load).
      if (!isInitial && from <= 5 && hasMoreRef.current && onLoadMoreRef.current && !loadingMoreRef.current) {
        onLoadMoreRef.current()
      }

      // Keep VP bars aligned with chart's current price-axis zoom level.
      updateVisiblePriceRange()
    }

    // Compute initial VP without triggering loadMore
    handleRangeChange(true)

    const rangeHandler = () => handleRangeChange(false)
    ch.timeScale().subscribeVisibleLogicalRangeChange(rangeHandler)
    return () => ch.timeScale().unsubscribeVisibleLogicalRangeChange(rangeHandler)
  }, [klines, updateVisiblePriceRange])

  // Update price range on crosshair move and resize
  useEffect(() => {
    const ch = chartRef.current
    if (!ch) return
    updateVisiblePriceRange()
    ch.subscribeCrosshairMove(updateVisiblePriceRange)
    return () => ch.unsubscribeCrosshairMove(updateVisiblePriceRange)
  }, [updateVisiblePriceRange])

  // Resize handler using ResizeObserver for reliable layout tracking
  useEffect(() => {
    const container = chartContainerRef.current
    if (!container) return

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (chartRef.current && width > 0 && height > 0) {
          chartRef.current.applyOptions({ width, height })
          updateVisiblePriceRange()
        }
      }
    })
    ro.observe(container)
    return () => ro.disconnect()
  }, [updateVisiblePriceRange])

  // MA lines
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    for (const s of maSeriesRefs.current) {
      chart.removeSeries(s)
    }
    maSeriesRefs.current = []

    if (showMA && klines.length > 0) {
      const periods = [5, 10, 20]
      for (let i = 0; i < periods.length; i++) {
        const maData = computeMALine(klines, periods[i])
        if (maData.length > 0) {
          const maSeries = chart.addSeries(LineSeries, {
            color: MA_COLORS[i],
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: true,
          })
          maSeries.setData(maData as LineData<Time>[])
          maSeriesRefs.current.push(maSeries)
        }
      }
    }
  }, [showMA, klines])

  // VWAP line
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    if (vwapSeriesRef.current) {
      chart.removeSeries(vwapSeriesRef.current)
      vwapSeriesRef.current = null
    }

    if (showVWAP && klines.length > 0) {
      const vwapData = computeVWAPLine(klines)
      if (vwapData.length > 0) {
        const vwapS = chart.addSeries(LineSeries, {
          color: VWAP_COLOR,
          lineWidth: 2,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: true,
        })
        vwapS.setData(vwapData as LineData<Time>[])
        vwapSeriesRef.current = vwapS
      }
    }
  }, [showVWAP, klines])

  // CYQ price lines
  useEffect(() => {
    const cds = candlestickSeriesRef.current
    if (!cds) return

    for (const pl of cyqPriceLinesRef.current) {
      cds.removePriceLine(pl)
    }
    cyqPriceLinesRef.current = []

    if (showCYQ && cyqInfo) {
      const newLines: typeof cyqPriceLinesRef.current = []

      if (cyqInfo.avg_cost > 0) {
        newLines.push(cds.createPriceLine({
          price: cyqInfo.avg_cost, color: '#38bdf8', lineWidth: 2,
          lineStyle: 2, axisLabelVisible: true, title: 'AVG',
        }))
      }
      if (cyqInfo.cost_90_low > 0) {
        newLines.push(cds.createPriceLine({
          price: cyqInfo.cost_90_low, color: '#f59e0b', lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: '90L',
        }))
      }
      if (cyqInfo.cost_90_high > 0) {
        newLines.push(cds.createPriceLine({
          price: cyqInfo.cost_90_high, color: '#f59e0b', lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: '90H',
        }))
      }
      if (cyqInfo.cost_70_low > 0) {
        newLines.push(cds.createPriceLine({
          price: cyqInfo.cost_70_low, color: '#06b6d4', lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: '70L',
        }))
      }
      if (cyqInfo.cost_70_high > 0) {
        newLines.push(cds.createPriceLine({
          price: cyqInfo.cost_70_high, color: '#06b6d4', lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: '70H',
        }))
      }

      cyqPriceLinesRef.current = newLines
    }

    return () => {
      const cds = candlestickSeriesRef.current
      if (cds) {
        for (const pl of cyqPriceLinesRef.current) {
          cds.removePriceLine(pl)
        }
        cyqPriceLinesRef.current = []
      }
    }
  }, [showCYQ, cyqInfo])

  // VP Y-axis: use chart's visible price range so bars align with
  // the candlestick chart regardless of price-axis zoom level.
  const displayPriceMin = visiblePriceRange?.low ?? activeVP?.price_min ?? 0
  const displayPriceMax = visiblePriceRange?.high ?? activeVP?.price_max ?? 0

  // Volume Profile panel data (visible range only)
  const profileBars = useMemo(() => {
    if (!activeVP || !activeVP.profile.length) return []
    if (getMaxProfileVolume(activeVP.profile) === 0) return []
    const pr = displayPriceMax - displayPriceMin || 1
    return activeVP.profile.map((p) => ({
      ...p,
      yPct: ((p.price - displayPriceMin) / pr) * 100,
    }))
  }, [activeVP, displayPriceMin, displayPriceMax])

  const calcYPos = useCallback(
    (price: number) => {
      const pr = displayPriceMax - displayPriceMin
      if (pr <= 0) return -1
      return ((price - displayPriceMin) / pr) * 100
    },
    [displayPriceMin, displayPriceMax],
  )

  const pocYPos = useMemo(() => activeVP?.poc ? calcYPos(activeVP.poc.price) : -1, [activeVP, calcYPos])
  const vahYPos = useMemo(() => activeVP ? calcYPos(activeVP.value_area.vah) : -1, [activeVP, calcYPos])
  const valYPos = useMemo(() => activeVP ? calcYPos(activeVP.value_area.val) : -1, [activeVP, calcYPos])
  const vwapYPos = useMemo(() => activeVP?.vwap ? calcYPos(activeVP.vwap) : -1, [activeVP, calcYPos])

  const hvnYPositions = useMemo(() => {
    if (!activeVP?.hvn_levels?.length) return []
    return activeVP.hvn_levels
      .filter((p) => p >= displayPriceMin && p <= displayPriceMax)
      .map(calcYPos)
      .filter((y) => y >= 0)
  }, [activeVP, calcYPos, displayPriceMin, displayPriceMax])

  const lvnYPositions = useMemo(() => {
    if (!activeVP?.lvn_levels?.length) return []
    return activeVP.lvn_levels
      .filter((p) => p >= displayPriceMin && p <= displayPriceMax)
      .map(calcYPos)
      .filter((y) => y >= 0)
  }, [activeVP, calcYPos, displayPriceMin, displayPriceMax])

  const cyqYPositions = useMemo(() => {
    if (!cyqInfo || displayPriceMin === displayPriceMax) return {}
    const result: Record<string, { y: number; label: string; color: string }> = {}
    if (cyqInfo.avg_cost > 0) result.avg = { y: calcYPos(cyqInfo.avg_cost), label: 'AVG', color: '#38bdf8' }
    if (cyqInfo.cost_90_high > 0) result.c90h = { y: calcYPos(cyqInfo.cost_90_high), label: '90H', color: '#f59e0b' }
    if (cyqInfo.cost_90_low > 0) result.c90l = { y: calcYPos(cyqInfo.cost_90_low), label: '90L', color: '#f59e0b' }
    if (cyqInfo.cost_70_high > 0) result.c70h = { y: calcYPos(cyqInfo.cost_70_high), label: '70H', color: '#06b6d4' }
    if (cyqInfo.cost_70_low > 0) result.c70l = { y: calcYPos(cyqInfo.cost_70_low), label: '70L', color: '#06b6d4' }
    return result
  }, [cyqInfo, calcYPos, displayPriceMin, displayPriceMax])

  const showVPOverlay = activeVP && activeVP.profile.length > 0

  // GMM fit on Volume Profile distribution (when toggle is on)
  const gmmFit = useMemo(() => {
    if (!showGMM || !activeVP?.profile.length) return null
    return fitGMMToProfile(activeVP.profile, 5)
  }, [showGMM, activeVP])

  // GMM signal zones: compute buy/sell price boundaries from density thresholds
  const gmmSignal = useMemo(() => {
    if (!gmmFit?.curve.length) return { buyLine: null, sellLine: null, density: null, signal: null as string | null }
    const maxDensity = gmmFit.curve.reduce((m, p) => p.fitVolume > m ? p.fitVolume : m, 0)
    if (maxDensity <= 0) return { buyLine: null, sellLine: null, density: null, signal: null }
    const upper = gmmThreshold
    const lower = 1 - gmmThreshold

    const sorted = [...gmmFit.curve].sort((a, b) => a.price - b.price)
    let sellLine: number | null = null
    let buyLine: number | null = null

    for (const pt of sorted) {
      if (pt.fitVolume / maxDensity >= upper) { sellLine = pt.price; break }
    }
    for (let i = sorted.length - 1; i >= 0; i--) {
      if (sorted[i].fitVolume / maxDensity <= lower) { buyLine = sorted[i].price; break }
    }

    let density: number | null = null
    let signal: string | null = null
    if (currentPrice != null) {
      const prices = sorted.map((p) => p.price)
      const densities = sorted.map((p) => p.fitVolume)
      const idx = prices.findIndex((p) => p >= currentPrice)
      if (idx === -1) {
        density = densities[densities.length - 1] / maxDensity
      } else if (idx === 0) {
        density = densities[0] / maxDensity
      } else {
        const t = (currentPrice - prices[idx - 1]) / (prices[idx] - prices[idx - 1])
        density = (densities[idx - 1] + t * (densities[idx] - densities[idx - 1])) / maxDensity
      }
      if (density >= upper) signal = 'sell'
      else if (density <= lower) signal = 'buy'
      else signal = 'neutral'
    }

    return { buyLine, sellLine, density, signal }
  }, [gmmFit, gmmThreshold, currentPrice])

  // Shared X-axis max: include GMM fit peak so nothing clips
  const vpxMax = useMemo(() => {
    const vpMax = getMaxProfileVolume(activeVP?.profile || [])
    const fitMax = gmmFit?.curve.reduce((m: number, p) => p.fitVolume > m ? p.fitVolume : m, 0) ?? 0
    return Math.max(vpMax, fitMax, 1)
  }, [activeVP, gmmFit])

  const barWidth = useCallback(
    (v: number) => (v / vpxMax) * 85,
    [vpxMax],
  )

  return (
    <div className="flex gap-0 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] overflow-hidden" style={{ height: 520 }}>
      {/* Candlestick chart + Volume Profile overlay */}
      <div className="relative flex-1 min-w-0" style={{ height: 520 }}>
        <div ref={chartContainerRef} className="absolute inset-0" />

        {/* Volume Profile overlay — semi-transparent bars extending from right price axis */}
        {showVPOverlay && (
          <div className="absolute inset-0 pointer-events-none z-10" style={{ right: 60 }}>
            {/* Volume axis: tick marks showing volume scale */}
            {vpxMax > 0 && (
              <div className="absolute left-0 right-0 top-2 h-4 z-20 pointer-events-none">
                {[0, 1, 2, 3, 4].map((i) => {
                  const vol = (vpxMax / 4) * i
                  const label = vol >= 10000 ? `${(vol / 10000).toFixed(1)}万` : `${vol.toFixed(0)}`
                  const w = barWidth(vol)
                  return (
                    <div key={i} className="absolute top-0" style={{ right: `${w}%` }}>
                      <div className="h-2 border-l border-blue-400/30" />
                      <span className="absolute top-2 -translate-x-1/2 text-[8px] text-blue-400/60 font-mono whitespace-nowrap">
                        {i === 0 ? '0' : label}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
            {profileBars.map((bar, i) => (
              <div
                key={i}
                className="absolute right-0 bg-blue-400/15 hover:bg-blue-400/25"
                style={{
                  bottom: `${bar.yPct}%`,
                  height: `${Math.max(100 / profileBars.length, 0.8)}%`,
                  width: `${barWidth(bar.volume)}%`,
                }}
              />
            ))}

            {/* POC overlay marker */}
            {pocYPos >= 0 && (
              <div
                className="absolute left-0 right-0 border-t-2 border-dashed"
                style={{ bottom: `${pocYPos}%`, borderColor: 'var(--up)', opacity: 0.6 }}
              />
            )}

            {/* GMM multi-peak fit curve on Volume Profile */}
            {gmmFit && gmmFit.peaks.length > 0 && (() => {
              const priceMin = displayPriceMin || (activeVP?.price_min ?? 0)
              const priceMax = displayPriceMax || (activeVP?.price_max ?? 1)
              const priceRng = priceMax - priceMin || 1
              return (
                <>
                  {gmmFit.curve.map((pt, i) => {
                    const y = ((pt.price - priceMin) / priceRng) * 100
                    if (y < 0 || y > 100) return null
                    const w = barWidth(pt.fitVolume)
                    return (
                      <div
                        key={`gc-${i}`}
                        className="absolute pointer-events-none"
                        style={{ right: `${w}%`, bottom: `${y}%`, width: 2, height: 2, background: '#9333ea', opacity: 0.7, transform: 'translate(0,50%)' }}
                      />
                    )
                  })}
                  {gmmFit.peaks.map((pk, i) => {
                    const y = ((pk.price - priceMin) / priceRng) * 100
                    if (y < 0 || y > 100) return null
                    return (
                      <div
                        key={`gr-${i}`}
                        className="absolute left-0 right-0 pointer-events-none"
                        style={{ bottom: `${y}%`, borderTop: '1px dashed #a855f7', opacity: 0.25 }}
                      />
                    )
                  })}
                  {gmmFit.peaks.map((pk, i) => {
                    const y = ((pk.price - priceMin) / priceRng) * 100
                    if (y < 0 || y > 100) return null
                    const w = barWidth(pk.volume)
                    return (
                      <span
                        key={`gl-${i}`}
                        className="absolute z-20 pointer-events-none text-[9px] font-bold text-purple-700 bg-white/80 px-1 rounded whitespace-nowrap"
                        style={{ bottom: `${y}%`, right: `${w}%` }}
                      >
                        ¥{pk.price.toFixed(2)}
                      </span>
                    )
                  })}

                  {/* GMM signal zones: buy/sell boundary lines */}
                  {gmmSignal.buyLine != null && (() => {
                    const y = ((gmmSignal.buyLine - priceMin) / priceRng) * 100
                    if (y < 0 || y > 100) return null
                    return (
                      <div
                        className="absolute left-0 right-0 pointer-events-none flex items-center gap-1"
                        style={{ bottom: `${y}%` }}
                      >
                        <div className="flex-1 border-t border-dashed" style={{ borderColor: 'var(--up)', opacity: 0.5 }} />
                        <span className="text-[8px] text-[var(--up)] font-medium whitespace-nowrap pr-1">买入区</span>
                      </div>
                    )
                  })()}
                  {gmmSignal.sellLine != null && (() => {
                    const y = ((gmmSignal.sellLine - priceMin) / priceRng) * 100
                    if (y < 0 || y > 100) return null
                    return (
                      <div
                        className="absolute left-0 right-0 pointer-events-none flex items-center gap-1"
                        style={{ bottom: `${y}%` }}
                      >
                        <div className="flex-1 border-t border-dashed" style={{ borderColor: 'var(--down)', opacity: 0.5 }} />
                        <span className="text-[8px] text-[var(--down)] font-medium whitespace-nowrap pr-1">卖出区</span>
                      </div>
                    )
                  })()}

                  {/* Current price density badge */}
                  {gmmSignal.density != null && (
                    <div className="absolute top-2 right-2 z-30 pointer-events-none">
                      <span className={`
                        text-[10px] font-bold px-1.5 py-0.5 rounded
                        ${gmmSignal.signal === 'buy' ? 'bg-[var(--up-soft)] text-[var(--up)]' : ''}
                        ${gmmSignal.signal === 'sell' ? 'bg-[var(--down-soft)] text-[var(--down)]' : ''}
                        ${gmmSignal.signal === 'neutral' ? 'bg-slate-100 text-slate-600' : ''}
                      `}>
                        密度 {(gmmSignal.density * 100).toFixed(0)}%
                        {gmmSignal.signal === 'buy' && ' 买入'}
                        {gmmSignal.signal === 'sell' && ' 卖出'}
                      </span>
                    </div>
                  )}
                </>
              )
            })()}
          </div>
        )}
      </div>

      {/* CYQ chip distribution panel (right side) — shown when CYQ toggle is ON */}
      {showCYQ && cyqInfo && displayPriceMin > 0 && (
        <div className="relative flex w-32 flex-col border-l border-[var(--border-subtle)] bg-[var(--surface-subtle)] shrink-0" style={{ height: 520 }}>
          <div className="flex-none border-b border-[var(--border-subtle)] px-2 py-1.5 text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">
            筹码
          </div>

          <div className="relative flex-1 overflow-hidden">
            <div className="absolute inset-0 pointer-events-none">
              {/* 90% cost range band */}
              {cyqInfo.cost_90_low > 0 && cyqInfo.cost_90_high > 0 && (
                <div
                  className="absolute left-2 right-0 bg-amber-200/50 border-l-2 border-amber-400/60"
                  style={{
                    bottom: `${calcYPos(cyqInfo.cost_90_low)}%`,
                    top: `${100 - calcYPos(cyqInfo.cost_90_high)}%`,
                  }}
                >
                  <span className="absolute right-1 top-1/2 -translate-y-1/2 text-[8px] font-medium text-amber-700">90%</span>
                </div>
              )}

              {/* 70% cost range band */}
              {cyqInfo.cost_70_low > 0 && cyqInfo.cost_70_high > 0 && (
                <div
                  className="absolute left-3 right-0 bg-cyan-200/60 border-l-2 border-cyan-400/60"
                  style={{
                    bottom: `${calcYPos(cyqInfo.cost_70_low)}%`,
                    top: `${100 - calcYPos(cyqInfo.cost_70_high)}%`,
                  }}
                >
                  <span className="absolute right-1 top-1/2 -translate-y-1/2 text-[8px] font-medium text-cyan-700">70%</span>
                </div>
              )}

              {/* Average cost line */}
              {cyqInfo.avg_cost > 0 && (
                <>
                  <div
                    className="absolute left-0 right-0 border-t-2 border-dashed z-10"
                    style={{ borderColor: '#38bdf8', bottom: `${calcYPos(cyqInfo.avg_cost)}%` }}
                  />
                  <span
                    className="absolute right-1 z-10 text-[8px] font-bold"
                    style={{ color: '#38bdf8', bottom: `${calcYPos(cyqInfo.avg_cost)}%`, transform: 'translateY(-50%)' }}
                  >
                    AVG
                  </span>
                </>
              )}
            </div>
          </div>

          {/* Info footer */}
          <div className="flex-none border-t border-[var(--border-subtle)] px-1.5 py-1.5 space-y-0.5">
            <div className="flex justify-between text-[9px]">
              <span className="text-[var(--text-dim)]">获利</span>
              <span className={cyqInfo.profit_ratio >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
                {(cyqInfo.profit_ratio * 100).toFixed(1)}%
              </span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[var(--text-dim)]">90%集中度</span>
              <span className="text-[var(--text-strong)]">{(cyqInfo.concentration_90 * 100).toFixed(2)}%</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[var(--text-dim)]">70%集中度</span>
              <span className="text-[var(--text-strong)]">{(cyqInfo.concentration_70 * 100).toFixed(2)}%</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
