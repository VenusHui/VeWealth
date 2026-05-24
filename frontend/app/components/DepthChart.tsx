'use client'

import { useEffect, useRef, useCallback, useMemo } from 'react'
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
} from '../lib/depthChartUtils'

interface DepthChartProps {
  klines: KlineDataPoint[]
  volumeProfile: VolumeProfileData | null
  cyqInfo: CyqInfo | null
  showMA: boolean
  showVWAP: boolean
  showGMM: boolean
  showCYQ: boolean
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
      },
      localization: {
        dateFormat: 'yyyy-MM-dd',
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

  // Resize handler using ResizeObserver for reliable layout tracking
  useEffect(() => {
    const container = chartContainerRef.current
    if (!container) return

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (chartRef.current && width > 0 && height > 0) {
          chartRef.current.applyOptions({ width, height })
        }
      }
    })
    ro.observe(container)
    return () => ro.disconnect()
  }, [])

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

  // Volume Profile panel data
  const profileBars = useMemo(() => {
    if (!volumeProfile || !volumeProfile.profile.length) return []
    const maxVol = getMaxProfileVolume(volumeProfile.profile)
    if (maxVol === 0) return []
    const priceMin = volumeProfile.price_min
    const priceMax = volumeProfile.price_max
    const priceRange = priceMax - priceMin || 1
    return volumeProfile.profile.map((p) => ({
      ...p,
      barWidth: (p.volume / maxVol) * 100,
      yPct: ((p.price - priceMin) / priceRange) * 100,
    }))
  }, [volumeProfile])

  const calcYPos = useCallback(
    (price: number) => {
      if (!volumeProfile) return -1
      const priceRange = volumeProfile.price_max - volumeProfile.price_min
      if (priceRange <= 0) return -1
      return ((price - volumeProfile.price_min) / priceRange) * 100
    },
    [volumeProfile],
  )

  const pocYPos = useMemo(() => volumeProfile?.poc ? calcYPos(volumeProfile.poc.price) : -1, [volumeProfile, calcYPos])
  const vahYPos = useMemo(() => volumeProfile ? calcYPos(volumeProfile.value_area.vah) : -1, [volumeProfile, calcYPos])
  const valYPos = useMemo(() => volumeProfile ? calcYPos(volumeProfile.value_area.val) : -1, [volumeProfile, calcYPos])
  const vwapYPos = useMemo(() => volumeProfile?.vwap ? calcYPos(volumeProfile.vwap) : -1, [volumeProfile, calcYPos])

  const hvnYPositions = useMemo(() => {
    if (!volumeProfile?.hvn_levels?.length) return []
    return volumeProfile.hvn_levels
      .filter((p) => p >= volumeProfile.price_min && p <= volumeProfile.price_max)
      .map(calcYPos)
      .filter((y) => y >= 0)
  }, [volumeProfile, calcYPos])

  const lvnYPositions = useMemo(() => {
    if (!volumeProfile?.lvn_levels?.length) return []
    return volumeProfile.lvn_levels
      .filter((p) => p >= volumeProfile.price_min && p <= volumeProfile.price_max)
      .map(calcYPos)
      .filter((y) => y >= 0)
  }, [volumeProfile, calcYPos])

  const gmmCurvePoints = useMemo(() => {
    if (!volumeProfile?.fit_result?.fit_curve || !volumeProfile.profile.length) return []
    const fitCurve = volumeProfile.fit_result.fit_curve
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return []
    const maxFitVol = Math.max(...fitCurve.map((p) => p.fitVolume), 1)
    return fitCurve.map((p) => ({
      price: p.price,
      yPct: ((p.price - volumeProfile.price_min) / priceRange) * 100,
      widthPct: (p.fitVolume / maxFitVol) * 70,
    }))
  }, [volumeProfile])

  const gmmComponents = useMemo(() => {
    return volumeProfile?.fit_result?.components || []
  }, [volumeProfile])

  const cyqYPositions = useMemo(() => {
    if (!volumeProfile || !cyqInfo || volumeProfile.price_min === volumeProfile.price_max) return {}
    const result: Record<string, { y: number; label: string; color: string }> = {}
    if (cyqInfo.avg_cost > 0) result.avg = { y: calcYPos(cyqInfo.avg_cost), label: 'AVG', color: '#38bdf8' }
    if (cyqInfo.cost_90_high > 0) result.c90h = { y: calcYPos(cyqInfo.cost_90_high), label: '90H', color: '#f59e0b' }
    if (cyqInfo.cost_90_low > 0) result.c90l = { y: calcYPos(cyqInfo.cost_90_low), label: '90L', color: '#f59e0b' }
    if (cyqInfo.cost_70_high > 0) result.c70h = { y: calcYPos(cyqInfo.cost_70_high), label: '70H', color: '#06b6d4' }
    if (cyqInfo.cost_70_low > 0) result.c70l = { y: calcYPos(cyqInfo.cost_70_low), label: '70L', color: '#06b6d4' }
    return result
  }, [volumeProfile, cyqInfo, calcYPos])

  const showVPOverlay = volumeProfile && volumeProfile.profile.length > 0

  return (
    <div className="flex gap-0 rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.78)] overflow-hidden" style={{ height: 520 }}>
      {/* Candlestick chart + Volume Profile overlay */}
      <div className="relative flex-1 min-w-0" style={{ height: 520 }}>
        <div ref={chartContainerRef} className="absolute inset-0" />

        {/* Volume Profile overlay — semi-transparent bars extending from right price axis */}
        {showVPOverlay && (
          <div className="absolute inset-0 pointer-events-none z-10" style={{ right: 60 }}>
            {profileBars.map((bar, i) => (
              <div
                key={i}
                className="absolute right-0 bg-blue-400/15 hover:bg-blue-400/25"
                style={{
                  bottom: `${bar.yPct}%`,
                  height: `${Math.max(100 / profileBars.length, 0.8)}%`,
                  width: `${Math.min(bar.barWidth, 85)}%`,
                }}
              />
            ))}

            {/* POC overlay marker */}
            {pocYPos >= 0 && (
              <div
                className="absolute left-0 right-0 border-t-2 border-dashed border-red-400/60"
                style={{ bottom: `${pocYPos}%` }}
              />
            )}
          </div>
        )}
      </div>

      {/* Volume Profile panel (right side) */}
      {showVPOverlay && (
        <div className="relative flex w-32 flex-col border-l border-[var(--border-subtle)] bg-[rgba(248,250,252,0.7)] shrink-0" style={{ height: 520 }}>
          <div className="flex-none border-b border-[var(--border-subtle)] px-2 py-1.5 text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">
            筹码峰
          </div>

          <div className="relative flex-1 overflow-hidden">
            {/* Volume bars */}
            <div className="absolute inset-0">
              {profileBars.map((bar, i) => (
                <div
                  key={i}
                  className="absolute right-0 h-px bg-blue-400/50"
                  style={{
                    bottom: `${bar.yPct}%`,
                    width: `${Math.max(bar.barWidth, 2)}%`,
                    minWidth: '2px',
                  }}
                />
              ))}
            </div>

            {/* Overlay markers */}
            <div className="absolute inset-0 pointer-events-none">
              {valYPos >= 0 && vahYPos >= 0 && (
                <div
                  className="absolute left-2 right-0 bg-blue-400/10 border-l-2 border-blue-400/30"
                  style={{ bottom: `${valYPos}%`, top: `${100 - vahYPos}%` }}
                />
              )}

              {pocYPos >= 0 && (
                <>
                  <div className="absolute left-0 right-0 border-t-2 border-dashed border-red-500 z-10" style={{ bottom: `${pocYPos}%` }} />
                  <div className="absolute right-1 z-10 -translate-y-1/2 rounded bg-red-500 px-1 text-[9px] font-bold text-white" style={{ bottom: `${pocYPos}%` }}>
                    POC
                  </div>
                </>
              )}

              {vahYPos >= 0 && (
                <div className="absolute left-0 right-0 border-t border-dashed border-blue-400/50 z-10" style={{ bottom: `${vahYPos}%` }} />
              )}
              {valYPos >= 0 && (
                <div className="absolute left-0 right-0 border-t border-dashed border-blue-400/50 z-10" style={{ bottom: `${valYPos}%` }} />
              )}

              {hvnYPositions.map((y, i) => (
                <div key={`hvn-${i}`} className="absolute z-10 flex items-center" style={{ bottom: `${y}%` }}>
                  <div className="h-1.5 w-1.5 rounded-full bg-blue-600" />
                  <span className="ml-0.5 text-[7px] text-blue-600 font-medium">HVN</span>
                </div>
              ))}

              {lvnYPositions.map((y, i) => (
                <div key={`lvn-${i}`} className="absolute z-10 flex items-center" style={{ bottom: `${y}%` }}>
                  <div className="h-1.5 w-1.5 rounded-full border border-gray-400 bg-gray-300" />
                  <span className="ml-0.5 text-[7px] text-gray-500 font-medium">LVN</span>
                </div>
              ))}

              {showCYQ && Object.values(cyqYPositions).map((item, i) => (
                <div key={i} className="absolute left-0 right-0 z-10" style={{ bottom: `${item.y}%` }}>
                  <div className="border-t border-dashed" style={{ borderColor: item.color }} />
                  <span className="absolute right-0.5 -translate-y-1/2 text-[8px] font-medium" style={{ color: item.color }}>
                    {item.label}
                  </span>
                </div>
              ))}

              {showVWAP && vwapYPos >= 0 && (
                <div className="absolute left-0 right-0 z-10 border-t-2 border-dashed" style={{ borderColor: VWAP_COLOR, bottom: `${vwapYPos}%` }} />
              )}

              {/* GMM fit curve */}
              {showGMM && gmmCurvePoints.length > 0 && (
                <svg className="absolute inset-0 z-20" preserveAspectRatio="none">
                  <polyline
                    points={gmmCurvePoints.map((p) => `${70 - p.widthPct},${100 - p.yPct}`).join(' ')}
                    fill="none" stroke="#9333ea" strokeWidth="1.5" strokeDasharray="4,2" opacity="0.8"
                  />
                  {gmmComponents.map((c, i) => {
                    const y = calcYPos(c.mean)
                    if (y < 0 || y > 100) return null
                    return (
                      <g key={i}>
                        <line x1="0" y1={`${100 - y}%`} x2="70" y2={`${100 - y}%`} stroke="#9333ea" strokeWidth="1" strokeDasharray="2,2" opacity="0.5" />
                        <text x="72" y={`${100 - y}%`} fill="#9333ea" fontSize="8" dominantBaseline="middle">
                          μ{c.mean.toFixed(1)}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              )}
            </div>
          </div>

          {/* Price labels */}
          <div className="flex-none border-t border-[var(--border-subtle)] px-1 py-1 text-center text-[9px] text-[var(--text-dim)]">
            <div>¥{volumeProfile.price_max.toFixed(2)}</div>
            <div className="flex justify-between">
              <span>¥{volumeProfile.price_min.toFixed(2)}</span>
              <span>Vol</span>
            </div>
          </div>

          {/* GMM component info */}
          {showGMM && gmmComponents.length > 0 && (
            <div className="flex-none border-t border-[var(--border-subtle)] px-1.5 py-1">
              {gmmComponents.map((c, i) => (
                <div key={i} className="flex items-center justify-between text-[9px]">
                  <span className="font-medium text-purple-700">μ{c.mean.toFixed(1)}</span>
                  <span className="text-[var(--text-dim)]">σ{c.std.toFixed(2)}</span>
                  <span className="text-[var(--text-dim)]">{((c.weight ?? 0) * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
