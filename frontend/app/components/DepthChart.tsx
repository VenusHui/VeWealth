'use client'

import { useEffect, useRef, useCallback, useMemo } from 'react'
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type LineData,
  type HistogramData,
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
  ohlcvToVolumeData,
  computeMALine,
  computeVWAPLine,
  getMaxProfileVolume,
} from '../lib/depthChartUtils'

interface DepthChartProps {
  klines: KlineDataPoint[]
  volumeProfile: VolumeProfileData | null
  cyqInfo: CyqInfo | null
  period: string
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
  period,
  showMA,
  showVWAP,
  showGMM,
  showCYQ,
}: DepthChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const maSeriesRefs = useRef<ISeriesApi<'Line'>[]>([])
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const cyqPriceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([])

  const candlestickData = useMemo(() => ohlcvToCandlestickData(klines), [klines])
  const volumeData = useMemo(() => ohlcvToVolumeData(klines), [klines])

  const createChartInstance = useCallback(() => {
    if (!chartContainerRef.current) return

    const container = chartContainerRef.current
    container.innerHTML = ''

    const chart = createChart(container, {
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
        scaleMargins: { top: 0.05, bottom: 0.25 },
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

    // Pane 1: Candlestick (main price chart)
    const cds = chart.addSeries(CandlestickSeries, {
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderUpColor: UP_BORDER,
      borderDownColor: DOWN_BORDER,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
    })
    cds.setData(candlestickData as CandlestickData<Time>[])

    // Pane 2: Volume histogram (below)
    const vs = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    vs.setData(volumeData as HistogramData<Time>[])
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0.0 },
    })

    chartRef.current = chart
    candlestickSeriesRef.current = cds
    volumeSeriesRef.current = vs
    maSeriesRefs.current = []
    vwapSeriesRef.current = null
    cyqPriceLinesRef.current = []
  }, [candlestickData, volumeData])

  // Create chart on mount and when klines change
  useEffect(() => {
    createChartInstance()

    return () => {
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [createChartInstance])

  // Update candlestick/volume data when it changes
  useEffect(() => {
    if (candlestickSeriesRef.current) {
      candlestickSeriesRef.current.setData(candlestickData as CandlestickData<Time>[])
    }
    if (volumeSeriesRef.current) {
      volumeSeriesRef.current.setData(volumeData as HistogramData<Time>[])
    }
  }, [candlestickData, volumeData])

  // MA lines
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    // Remove existing MA series
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

    // Clean up previously created price lines
    for (const pl of cyqPriceLinesRef.current) {
      cds.removePriceLine(pl)
    }
    cyqPriceLinesRef.current = []

    if (showCYQ && cyqInfo) {
      const newLines: typeof cyqPriceLinesRef.current = []

      if (cyqInfo.avg_cost > 0) {
        newLines.push(
          cds.createPriceLine({
            price: cyqInfo.avg_cost,
            color: '#38bdf8',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'AVG',
          }),
        )
      }
      if (cyqInfo.cost_90_low > 0) {
        newLines.push(
          cds.createPriceLine({
            price: cyqInfo.cost_90_low,
            color: '#f59e0b',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '90L',
          }),
        )
      }
      if (cyqInfo.cost_90_high > 0) {
        newLines.push(
          cds.createPriceLine({
            price: cyqInfo.cost_90_high,
            color: '#f59e0b',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '90H',
          }),
        )
      }
      if (cyqInfo.cost_70_low > 0) {
        newLines.push(
          cds.createPriceLine({
            price: cyqInfo.cost_70_low,
            color: '#06b6d4',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '70L',
          }),
        )
      }
      if (cyqInfo.cost_70_high > 0) {
        newLines.push(
          cds.createPriceLine({
            price: cyqInfo.cost_70_high,
            color: '#06b6d4',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '70H',
          }),
        )
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

  // Resize handler
  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        })
      }
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  // Volume Profile panel data
  const profileBars = useMemo(() => {
    if (!volumeProfile || !volumeProfile.profile.length) return []
    const maxVol = getMaxProfileVolume(volumeProfile.profile)
    if (maxVol === 0) return []
    const priceMin = volumeProfile.price_min
    const priceMax = volumeProfile.price_max
    const priceRange = priceMax - priceMin
    return volumeProfile.profile.map((p) => ({
      ...p,
      barWidth: (p.volume / maxVol) * 100,
      yPct: ((p.price - priceMin) / (priceRange || 1)) * 100,
    }))
  }, [volumeProfile])

  const pocYPos = useMemo(() => {
    if (!volumeProfile || !volumeProfile.poc || volumeProfile.poc.price === 0) return -1
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return -1
    return ((volumeProfile.poc.price - volumeProfile.price_min) / priceRange) * 100
  }, [volumeProfile])

  const vahYPos = useMemo(() => {
    if (!volumeProfile) return -1
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return -1
    return ((volumeProfile.value_area.vah - volumeProfile.price_min) / priceRange) * 100
  }, [volumeProfile])

  const valYPos = useMemo(() => {
    if (!volumeProfile) return -1
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return -1
    return ((volumeProfile.value_area.val - volumeProfile.price_min) / priceRange) * 100
  }, [volumeProfile])

  const vwapYPos = useMemo(() => {
    if (!volumeProfile || volumeProfile.vwap <= 0) return -1
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return -1
    return ((volumeProfile.vwap - volumeProfile.price_min) / priceRange) * 100
  }, [volumeProfile])

  const cyqYPositions = useMemo(() => {
    if (!volumeProfile || !cyqInfo || volumeProfile.price_min === volumeProfile.price_max) return {}
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    const calcY = (price: number) => ((price - volumeProfile.price_min) / priceRange) * 100
    const result: Record<string, { y: number; label: string; color: string }> = {}
    if (cyqInfo.avg_cost > 0) {
      result.avg = { y: calcY(cyqInfo.avg_cost), label: 'AVG', color: '#38bdf8' }
    }
    if (cyqInfo.cost_90_high > 0) {
      result.c90h = { y: calcY(cyqInfo.cost_90_high), label: '90H', color: '#f59e0b' }
    }
    if (cyqInfo.cost_90_low > 0) {
      result.c90l = { y: calcY(cyqInfo.cost_90_low), label: '90L', color: '#f59e0b' }
    }
    if (cyqInfo.cost_70_high > 0) {
      result.c70h = { y: calcY(cyqInfo.cost_70_high), label: '70H', color: '#06b6d4' }
    }
    if (cyqInfo.cost_70_low > 0) {
      result.c70l = { y: calcY(cyqInfo.cost_70_low), label: '70L', color: '#06b6d4' }
    }
    return result
  }, [volumeProfile, cyqInfo])

  // GMM fit curve positions on Volume Profile panel
  const gmmCurvePoints = useMemo(() => {
    if (!volumeProfile?.fit_result?.fit_curve || !volumeProfile.profile.length) return []
    const fitCurve = volumeProfile.fit_result.fit_curve
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return []
    const maxFitVol = Math.max(...fitCurve.map((p) => p.fitVolume), 1)
    return fitCurve.map((p) => ({
      price: p.price,
      yPct: ((p.price - volumeProfile.price_min) / priceRange) * 100,
      widthPct: (p.fitVolume / maxFitVol) * 70, // scale relative to profile width
    }))
  }, [volumeProfile])

  const gmmComponents = useMemo(() => {
    if (!volumeProfile?.fit_result?.components) return []
    return volumeProfile.fit_result.components
  }, [volumeProfile])

  // HVN/LVN Y positions on Volume Profile panel
  const hvnYPositions = useMemo(() => {
    if (!volumeProfile || !volumeProfile.hvn_levels.length) return []
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return []
    return volumeProfile.hvn_levels
      .filter((p) => p >= volumeProfile.price_min && p <= volumeProfile.price_max)
      .map((p) => ((p - volumeProfile.price_min) / priceRange) * 100)
  }, [volumeProfile])

  const lvnYPositions = useMemo(() => {
    if (!volumeProfile || !volumeProfile.lvn_levels.length) return []
    const priceRange = volumeProfile.price_max - volumeProfile.price_min
    if (priceRange <= 0) return []
    return volumeProfile.lvn_levels
      .filter((p) => p >= volumeProfile.price_min && p <= volumeProfile.price_max)
      .map((p) => ((p - volumeProfile.price_min) / priceRange) * 100)
  }, [volumeProfile])

  return (
    <div className="flex flex-col lg:flex-row gap-0 rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.78)] overflow-hidden" style={{ minHeight: 420 }}>
      {/* Candlestick + Volume chart */}
      <div ref={chartContainerRef} className="flex-1 min-w-0" style={{ height: 480 }} />

      {/* Volume Profile panel (right side on desktop, bottom on mobile) */}
      {volumeProfile && volumeProfile.profile.length > 0 && (
        <div className="relative flex lg:w-32 w-full lg:flex-col flex-row border-t lg:border-t-0 lg:border-l border-[var(--border-subtle)] bg-[rgba(248,250,252,0.7)]" style={{ height: 480 }}>
          <div className="flex-none border-b border-[var(--border-subtle)] px-2 py-1.5 text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">
            筹码峰
          </div>

          {/* Profile bars container */}
          <div className="relative flex-1 overflow-hidden">
            {/* Blue-tinted background */}
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
              {/* Value Area shading */}
              {valYPos > 0 && vahYPos > 0 && (
                <div
                  className="absolute left-2 right-0 bg-blue-400/10 border-l-2 border-blue-400/30"
                  style={{ bottom: `${valYPos}%`, top: `${100 - vahYPos}%` }}
                />
              )}

              {/* POC line */}
              {pocYPos > 0 && (
                <>
                  <div
                    className="absolute left-0 right-0 border-t-2 border-dashed border-red-500 z-10"
                    style={{ bottom: `${pocYPos}%` }}
                  />
                  <div
                    className="absolute right-1 z-10 -translate-y-1/2 rounded bg-red-500 px-1 text-[9px] font-bold text-white"
                    style={{ bottom: `${pocYPos}%` }}
                  >
                    POC
                  </div>
                </>
              )}

              {/* VAH line */}
              {vahYPos > 0 && (
                <div
                  className="absolute left-0 right-0 border-t border-dashed border-blue-400/50 z-10"
                  style={{ bottom: `${vahYPos}%` }}
                />
              )}

              {/* VAL line */}
              {valYPos > 0 && (
                <div
                  className="absolute left-0 right-0 border-t border-dashed border-blue-400/50 z-10"
                  style={{ bottom: `${valYPos}%` }}
                />
              )}

              {/* HVN markers */}
              {hvnYPositions.map((y, i) => (
                <div
                  key={`hvn-${i}`}
                  className="absolute z-10 flex items-center"
                  style={{ bottom: `${y}%` }}
                >
                  <div className="h-1.5 w-1.5 rounded-full bg-blue-600" />
                  <span className="ml-0.5 text-[7px] text-blue-600 font-medium">HVN</span>
                </div>
              ))}

              {/* LVN markers */}
              {lvnYPositions.map((y, i) => (
                <div
                  key={`lvn-${i}`}
                  className="absolute z-10 flex items-center"
                  style={{ bottom: `${y}%` }}
                >
                  <div className="h-1.5 w-1.5 rounded-full border border-gray-400 bg-gray-300" />
                  <span className="ml-0.5 text-[7px] text-gray-500 font-medium">LVN</span>
                </div>
              ))}

              {/* CYQ lines */}
              {showCYQ && Object.values(cyqYPositions).map((item, i) => (
                <div key={i} className="relative z-10" style={{ bottom: `${item.y}%`, position: 'absolute', left: 0, right: 0 }}>
                  <div className="border-t border-dashed" style={{ borderColor: item.color }} />
                  <span
                    className="absolute right-0.5 -translate-y-1/2 text-[8px] font-medium"
                    style={{ color: item.color }}
                  >
                    {item.label}
                  </span>
                </div>
              ))}

              {/* VWAP marker on profile */}
              {showVWAP && vwapYPos > 0 && (
                <div
                  className="absolute left-0 right-0 z-10 border-t-2 border-dashed"
                  style={{ borderColor: VWAP_COLOR, bottom: `${vwapYPos}%` }}
                />
              )}

              {/* GMM fit curve overlay */}
              {showGMM && gmmCurvePoints.length > 0 && (
                <svg className="absolute inset-0 z-20 pointer-events-none" preserveAspectRatio="none">
                  <polyline
                    points={gmmCurvePoints
                      .map((p) => `${70 - p.widthPct},${100 - p.yPct}`)
                      .join(' ')}
                    fill="none"
                    stroke="#9333ea"
                    strokeWidth="1.5"
                    strokeDasharray="4,2"
                    opacity="0.8"
                  />
                  {/* GMM component mean lines */}
                  {gmmComponents.map((c, i) => {
                    const priceRange = volumeProfile ? volumeProfile.price_max - volumeProfile.price_min : 1
                    const y = ((c.mean - (volumeProfile?.price_min || 0)) / (priceRange || 1)) * 100
                    if (y < 0 || y > 100) return null
                    return (
                      <g key={i}>
                        <line x1="0" y1={`${100 - y}%`} x2="70" y2={`${100 - y}%`}
                          stroke="#9333ea" strokeWidth="1" strokeDasharray="2,2" opacity="0.5" />
                        <text x="72" y={`${100 - y}%`} fill="#9333ea" fontSize="8" dominantBaseline="middle">
                          μ{(c.mean).toFixed(1)}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              )}
            </div>
          </div>

          {/* Price labels at bottom and top */}
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
