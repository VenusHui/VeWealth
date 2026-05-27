'use client'

import Link from 'next/link'
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { isAuthenticated, getAuthHeader } from './lib/auth'
import { getApiBaseUrl } from './lib/api'
import { marketClassByValue, formatPct } from './lib/marketColors'
import { AppPage, EmptyState, InfoPill, MetricCard, PageHeader, QuickLinkCard, SurfaceCard } from './components/ui-shell'

const API_BASE_URL = getApiBaseUrl()

interface Quote {
  name?: string
  price?: number
  change_pct?: number
  change_amt?: number
}

interface WatchlistPreviewItem {
  id: number
  stock_code: string
  stock_name?: string
  current_price?: number | null
  change_pct?: number | null
}

interface AlertPreviewItem {
  id: number
  stock_code: string
  stock_name?: string
  current_price: number
  change_pct?: number | null
  created_at: string
}

const INDEX_CODES = ['000001', '399001', '399006']
const INDEX_LABELS: Record<string, string> = {
  '000001': '上证指数',
  '399001': '深证成指',
  '399006': '创业板指',
}

export default function HomePage() {
  const router = useRouter()
  const [mounted, setMounted] = useState(false)
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  // Market indices
  const [indices, setIndices] = useState<Record<string, Quote>>({})
  const [indicesLoading, setIndicesLoading] = useState(false)

  // Watchlist preview
  const [watchlistPreview, setWatchlistPreview] = useState<WatchlistPreviewItem[]>([])
  const [watchlistLoading, setWatchlistLoading] = useState(false)

  // Recent alerts
  const [alertsPreview, setAlertsPreview] = useState<AlertPreviewItem[]>([])
  const [alertsLoading, setAlertsLoading] = useState(false)

  const fetchDashboardData = useCallback(async () => {
    if (!isAuthenticated()) return
    const headers = getAuthHeader()

    // Fetch indices, watchlist, alerts in parallel
    setIndicesLoading(true)
    setWatchlistLoading(true)
    setAlertsLoading(true)

    try {
      const [indicesResp, watchlistResp, alertsResp] = await Promise.allSettled([
        axios.get(`${API_BASE_URL}/api/stock/quotes?codes=${INDEX_CODES.join(',')}`),
        axios.get(`${API_BASE_URL}/api/watchlist`, { headers }),
        axios.get(`${API_BASE_URL}/api/alerts?limit=5&offset=0`, { headers }),
      ])

      if (indicesResp.status === 'fulfilled' && indicesResp.value.data?.success) {
        setIndices(indicesResp.value.data.quotes || {})
      }

      if (watchlistResp.status === 'fulfilled' && watchlistResp.value.data?.success) {
        setWatchlistPreview((watchlistResp.value.data.data || []).slice(0, 5))
      }

      if (alertsResp.status === 'fulfilled' && alertsResp.value.data?.success) {
        setAlertsPreview(alertsResp.value.data.data || [])
      }
    } finally {
      setIndicesLoading(false)
      setWatchlistLoading(false)
      setAlertsLoading(false)
    }
  }, [])

  useEffect(() => {
    setMounted(true)
    const loggedIn = isAuthenticated()
    setIsLoggedIn(loggedIn)
    if (loggedIn) {
      fetchDashboardData()
    }
  }, [fetchDashboardData])

  // Auto-refresh indices every 30s
  useEffect(() => {
    if (!isLoggedIn) return
    const timer = setInterval(async () => {
      try {
        const resp = await axios.get(`${API_BASE_URL}/api/stock/quotes?codes=${INDEX_CODES.join(',')}`)
        if (resp.data?.success) setIndices(resp.data.quotes || {})
      } catch { /* silent */ }
    }, 30000)
    return () => clearInterval(timer)
  }, [isLoggedIn])

  if (!mounted) return null

  // Unauthenticated: landing page
  if (!isLoggedIn) {
    return (
      <AppPage>
        <PageHeader
          eyebrow="A-share workspace"
          title="分析、监控与回测，一个工作台完成。"
          badges={
            <>
              <InfoPill>价格分布</InfoPill>
              <InfoPill>预警监控</InfoPill>
              <InfoPill>策略回测</InfoPill>
            </>
          }
          actions={
            <Link href="/login" className="ve-button-primary">
              登录 / 注册
            </Link>
          }
        />

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <QuickLinkCard
            label="深度数据"
            title="量价分布与筹码分析"
            description="K 线叠加 Volume Profile 和 GMM 拟合曲线，快速识别支撑阻力位。"
            href="/depth"
            stats="无需登录"
          />
          <QuickLinkCard
            label="回测中心"
            title="从策略配置到结果钻取"
            description="创建任务、查看运行状态、下钻成交与快照，回溯策略表现。"
            href="/login"
            stats="需登录"
          />
        </div>
      </AppPage>
    )
  }

  // Authenticated: operational dashboard
  const upCount = watchlistPreview.filter((i) => (i.change_pct ?? 0) > 0).length
  const downCount = watchlistPreview.filter((i) => (i.change_pct ?? 0) < 0).length

  return (
    <AppPage>
      <PageHeader
        eyebrow="Dashboard"
        title="交易工作台"
        badges={
          <>
            <InfoPill>自选 {watchlistPreview.length} 只</InfoPill>
            <InfoPill>预警 {alertsPreview.length} 条</InfoPill>
          </>
        }
        actions={
          <div className="flex flex-wrap gap-2">
            <Link href="/depth" className="ve-button-primary">深度分析</Link>
            <Link href="/backtest" className="ve-button-secondary">创建回测</Link>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Left 2/3: Market + Watchlist */}
        <div className="space-y-5 lg:col-span-2">
          {/* Market indices */}
          <SurfaceCard title="市场概览" description="三大指数实时行情">
            {indicesLoading && Object.keys(indices).length === 0 ? (
              <div className="grid grid-cols-3 gap-3">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="animate-pulse rounded-2xl bg-slate-100 h-24" />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-3">
                {INDEX_CODES.map((code) => {
                  const q = indices[code]
                  return (
                    <div key={code} className="rounded-2xl border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.7)] p-4">
                      <div className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--text-dim)]">
                        {INDEX_LABELS[code] || code}
                      </div>
                      <div className="mt-2 text-xl font-semibold text-[var(--text-strong)]">
                        {q?.price != null ? `¥${q.price.toFixed(2)}` : '—'}
                      </div>
                      {q?.change_pct != null && (
                        <div className={`mt-1 text-sm font-medium ${marketClassByValue(q.change_pct)}`}>
                          {q.change_pct > 0 ? '+' : ''}{q.change_pct.toFixed(2)}%
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </SurfaceCard>

          {/* Watchlist preview */}
          <SurfaceCard
            title="自选股速览"
            description={`${upCount} 涨 · ${downCount} 跌`}
            actions={
              <Link href="/watchlist" className="ve-tab-button text-xs">
                查看全部 →
              </Link>
            }
          >
            {watchlistLoading && watchlistPreview.length === 0 ? (
              <div className="animate-pulse space-y-2">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-10 rounded-xl bg-slate-100" />
                ))}
              </div>
            ) : watchlistPreview.length > 0 ? (
              <div className="space-y-1">
                {watchlistPreview.map((item) => (
                  <Link
                    key={item.id}
                    href={`/depth?code=${item.stock_code}`}
                    className="flex items-center justify-between rounded-xl px-3 py-2.5 hover:bg-[rgba(240,253,250,0.5)] transition-colors"
                  >
                    <div className="min-w-0">
                      <span className="font-medium text-[var(--text-strong)]">{item.stock_name || item.stock_code}</span>
                      <span className="ml-2 text-xs text-[var(--text-dim)]">{item.stock_code}</span>
                    </div>
                    <div className="flex items-center gap-3 text-right">
                      <span className="font-semibold tabular-nums text-[var(--text-strong)]">
                        {item.current_price != null ? `¥${item.current_price.toFixed(2)}` : '—'}
                      </span>
                      {item.change_pct != null && (
                        <span className={`min-w-[4.5rem] text-sm font-medium tabular-nums ${marketClassByValue(item.change_pct)}`}>
                          {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                        </span>
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyState
                title="还没有自选股"
                description="在监控台中添加你关注的标的。"
                action={<Link href="/watchlist" className="ve-button-primary">前往监控台</Link>}
              />
            )}
          </SurfaceCard>
        </div>

        {/* Right 1/3: Alerts + Quick actions */}
        <div className="space-y-5">
          {/* Market trend summary */}
          <div className="grid grid-cols-3 gap-3 lg:grid-cols-1">
            <MetricCard label="自选股" value={watchlistPreview.length.toLocaleString()} meta="监控池" tone="brand" icon="◌" />
            <MetricCard label="上涨" value={upCount.toLocaleString()} meta="今日" tone="positive" icon="▲" />
            <MetricCard label="下跌" value={downCount.toLocaleString()} meta="今日" icon="▼" />
          </div>

          {/* Recent alerts */}
          <SurfaceCard
            title="最近预警"
            actions={
              alertsPreview.length > 0 ? (
                <Link href="/alerts" className="ve-tab-button text-xs">
                  查看全部 →
                </Link>
              ) : null
            }
          >
            {alertsLoading && alertsPreview.length === 0 ? (
              <div className="animate-pulse space-y-2">
                {[1, 2].map((i) => (
                  <div key={i} className="h-10 rounded-xl bg-slate-100" />
                ))}
              </div>
            ) : alertsPreview.length > 0 ? (
              <div className="space-y-2">
                {alertsPreview.map((alert) => (
                  <Link
                    key={alert.id}
                    href={`/depth?code=${alert.stock_code}`}
                    className="block rounded-xl border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.7)] px-3 py-2.5 hover:bg-[rgba(254,242,242,0.5)] transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-medium text-[var(--text-strong)]">{alert.stock_name || alert.stock_code}</span>
                        <span className="ml-2 text-xs text-[var(--text-dim)]">{alert.stock_code}</span>
                      </div>
                      {alert.change_pct != null && (
                        <span className={`text-sm font-medium ${marketClassByValue(alert.change_pct)}`}>
                          {alert.change_pct > 0 ? '+' : ''}{alert.change_pct.toFixed(2)}%
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex items-center justify-between text-xs text-[var(--text-dim)]">
                      <span>触发价 ¥{alert.current_price.toFixed(2)}</span>
                      <span>{new Date(alert.created_at).toLocaleString()}</span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无预警" description="当自选股价格触发阈值时，这里会显示记录。" />
            )}
          </SurfaceCard>
        </div>
      </div>
    </AppPage>
  )
}
