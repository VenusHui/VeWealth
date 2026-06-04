'use client'

import { useMemo, useState, useEffect, useRef, useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import axios from 'axios'
import { Alert, Button, Input, Spin } from 'antd'
import DepthChart from '../components/DepthChart'
import DepthToolbar from '../components/DepthToolbar'
import DepthStatistics from '../components/DepthStatistics'
import { AppPage, EmptyState, InfoPill, MetricCard, PageHeader, SurfaceCard } from '../components/ui-shell'
import { formatPct, marketClassByValue } from '../lib/marketColors'
import { getApiBaseUrl } from '../lib/api'
import { getAuthHeader, isAuthenticated } from '../lib/auth'
import type {
  KlineDataPoint,
  VolumeProfileData,
  CyqInfo,
} from '../lib/depthChartUtils'

const RECENT_SYMBOLS_KEY = 'vewealth_recent_symbols'
const MAX_RECENT = 5

interface StockSearchResult {
  code: string
  name: string
  current_price: number
}

interface StockInfo {
  code: string
  name: string
  industry: string
  total_shares: number
  float_shares: number
  mcap: number
  float_mcap: number
  list_date: string
  price: number
}

interface TencentQuote {
  name: string
  price: number
  last_close: number
  open: number
  change_amt: number
  change_pct: number
  high: number
  low: number
  amount_wan: number
  turnover_pct: number
  pe_ttm: number
  amplitude_pct: number
  mcap_yi: number
  float_mcap_yi: number
  pb: number
  limit_up: number
  limit_down: number
  vol_ratio: number
  pe_static: number
}

const API_BASE_URL = getApiBaseUrl()

const PERIOD_API_MAP: Record<string, string> = {
  '1min': '1',
  '5min': '5',
  '15min': '15',
  '30min': '30',
  '60min': '60',
  daily: '101',
}

function getPriceTone(quote: TencentQuote | null): 'positive' | 'warning' | 'default' {
  if (!quote) return 'default'
  if (quote.change_pct >= 0) return 'positive'
  return 'warning'
}

export default function DepthContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // Stock selection
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [showSearchResults, setShowSearchResults] = useState(false)

  // Watchlist + recent symbols for quick switching
  const [watchlistStocks, setWatchlistStocks] = useState<Array<{ code: string; name: string }>>([])
  const [recentSymbols, setRecentSymbols] = useState<Array<{ code: string; name: string }>>([])

  // Data
  const [klines, setKlines] = useState<KlineDataPoint[]>([])
  const [volumeProfile, setVolumeProfile] = useState<VolumeProfileData | null>(null)
  const [cyqInfo, setCyqInfo] = useState<CyqInfo | null>(null)
  const [stockInfo, setStockInfo] = useState<StockInfo | null>(null)
  const [tencentQuote, setTencentQuote] = useState<TencentQuote | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [totalOffset, setTotalOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const loadingMoreRef = useRef(false)

  // Toolbar state
  const [period, setPeriod] = useState('daily')
  const periodRef = useRef(period)
  periodRef.current = period
  const [adjust, setAdjust] = useState('qfq')
  const [showMA, setShowMA] = useState(true)
  const [showVWAP, setShowVWAP] = useState(true)
  const [showGMM, setShowGMM] = useState(false)
  const [showCYQ, setShowCYQ] = useState(false)

  // Stock search
  const handleSearch = async (keyword?: string) => {
    const kw = (keyword ?? searchKeyword).trim()
    if (!kw) {
      setSearchResults([])
      setShowSearchResults(false)
      return
    }
    try {
      setSearchLoading(true)
      const response = await axios.get(`${API_BASE_URL}/api/stock/search`, {
        params: { keyword: kw },
      })
      if (response.data.success) {
        setSearchResults(response.data.results)
        setShowSearchResults(true)
      }
    } catch {
      setSearchResults([])
    } finally {
      setSearchLoading(false)
    }
  }

  const handleSelectStock = (stock: StockSearchResult) => {
    setStockCode(stock.code)
    setStockName(stock.name)
    setSearchKeyword('')
    setSearchResults([])
    setShowSearchResults(false)
    router.replace(`/depth?code=${stock.code}`, { scroll: false })
    saveRecentSymbol(stock.code, stock.name)
  }

  const handleQuickSwitch = (code: string, name: string) => {
    setStockCode(code)
    setStockName(name)
    setSearchKeyword('')
    setSearchResults([])
    setShowSearchResults(false)
    router.replace(`/depth?code=${code}`, { scroll: false })
    saveRecentSymbol(code, name)
  }

  // Handle unified input: detect code vs name
  const handleUnifiedInput = () => {
    const kw = searchKeyword.trim()
    if (!kw) return
    if (/^\d{6}$/.test(kw)) {
      setStockCode(kw)
      setStockName('')
      setSearchKeyword('')
      setSearchResults([])
      setShowSearchResults(false)
      router.replace(`/depth?code=${kw}`, { scroll: false })
    } else {
      handleSearch(kw)
    }
  }

  // Load watchlist for quick-switch dropdown
  useEffect(() => {
    if (!isAuthenticated()) return
    axios
      .get(`${API_BASE_URL}/api/watchlist`, { headers: getAuthHeader() })
      .then((resp) => {
        if (resp.data?.success) {
          const items = (resp.data.data || []).map((item: { stock_code: string; stock_name?: string }) => ({
            code: item.stock_code,
            name: item.stock_name || item.stock_code,
          }))
          setWatchlistStocks(items)
        }
      })
      .catch(() => { /* silent */ })
  }, [])

  // Load recent symbols from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem(RECENT_SYMBOLS_KEY)
      if (raw) setRecentSymbols(JSON.parse(raw))
    } catch { /* ignore */ }
  }, [])

  // Save to recent symbols when stockCode changes (after successful data fetch)
  const saveRecentSymbol = useCallback((code: string, name: string) => {
    if (!code) return
    setRecentSymbols((prev) => {
      const filtered = prev.filter((s) => s.code !== code)
      const next = [{ code, name }, ...filtered].slice(0, MAX_RECENT)
      try { localStorage.setItem(RECENT_SYMBOLS_KEY, JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [])

  // URL param sync: read ?code= on mount
  useEffect(() => {
    const codeParam = searchParams.get('code')
    if (codeParam && /^\d{6}$/.test(codeParam)) {
      setStockCode(codeParam)
      setStockName('')
      setSearchKeyword('')
    }
  }, [searchParams])

  // Keyboard shortcuts (only when search input is not focused)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') return

      const key = e.key.toLowerCase()
      if ((e.ctrlKey || e.metaKey) && key === 'k') {
        e.preventDefault()
        document.getElementById('depth-search-input')?.focus()
        return
      }

      if (!stockCode) return

      switch (key) {
        case '1': setPeriod('1min'); break
        case '2': setPeriod('5min'); break
        case '3': setPeriod('15min'); break
        case '4': setPeriod('30min'); break
        case '5': setPeriod('60min'); break
        case '6': setPeriod('daily'); break
        case 'm': setShowMA((v) => !v); break
        case 'v': setShowVWAP((v) => !v); break
        case 'g': setShowGMM((v) => !v); break
        case 'c': setShowCYQ((v) => !v); break
        case 'escape': setShowSearchResults(false); break
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [stockCode])

  // Fetch depth data
  const handleFetchData = useCallback(async () => {
    if (!stockCode.trim()) {
      setError('请输入股票代码')
      return
    }
    if (!/^\d{6}$/.test(stockCode.trim())) {
      setError('股票代码格式错误，应为 6 位数字')
      return
    }
    try {
      setLoading(true)
      setError('')

      const apiPeriod = PERIOD_API_MAP[periodRef.current] || '5'

      const response = await axios.get(`${API_BASE_URL}/api/stock/depth`, {
        params: {
          symbol: stockCode.trim(),
          period: apiPeriod,
          start_date: '',
          end_date: '',
          adjust,
        },
      })

      if (response.data.success) {
        setKlines(response.data.klines || [])
        setVolumeProfile(response.data.volume_profile || null)
        setCyqInfo(response.data.cyq_info || null)
        setStockInfo(response.data.stock_info || null)
        setTencentQuote(response.data.tencent_quote || null)

        const name = response.data.tencent_quote?.name || response.data.stock_info?.name || ''
        if (name) {
          setStockName(name)
          saveRecentSymbol(stockCode.trim(), name)
        }
      } else {
        setError('获取数据失败')
      }
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail || '获取数据失败')
      } else {
        setError('获取数据失败')
      }
      setKlines([])
      setVolumeProfile(null)
      setCyqInfo(null)
    } finally {
      setLoading(false)
    }
  }, [stockCode, adjust, saveRecentSymbol])

  // Load more historical data when scrolling left.
  // Uses a ref for the loading guard to prevent race conditions from
  // rapid scroll events firing before React commits the state update.
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMore || !stockCode.trim()) return
    loadingMoreRef.current = true
    setLoadingMore(true)
    try {
      const newOffset = totalOffset + 500
      const apiPeriod = PERIOD_API_MAP[periodRef.current] || '5'
      const response = await axios.get(`${API_BASE_URL}/api/stock/kline`, {
        params: {
          symbol: stockCode.trim(),
          period: apiPeriod,
          start_date: '', end_date: '',
          adjust,
          offset: newOffset,
          count: 500,
        },
      })
      const newKlines = response.data.klines || []
      if (newKlines.length < 500) setHasMore(false)
      if (newKlines.length > 0) {
        setKlines((prev) => [...newKlines, ...prev])
        setTotalOffset(newOffset)
      }
    } catch {
      // silently fail for loadMore
    } finally {
      loadingMoreRef.current = false
      setLoadingMore(false)
    }
  }, [hasMore, stockCode, totalOffset, adjust])

  // Stable refs
  const handleFetchDataRef = useRef(handleFetchData)
  handleFetchDataRef.current = handleFetchData
  const loadMoreRef = useRef(loadMore)
  loadMoreRef.current = loadMore

  // Auto-fetch when toolbar params change (if stock is selected)
  useEffect(() => {
    setTotalOffset(0)
    setHasMore(true)
    if (stockCode.trim()) {
      handleFetchDataRef.current()
    }
  }, [period, adjust, stockCode])

  // Stable callback for loadMore (avoids stale closure in chart handler)
  const onLoadMore = useCallback(() => loadMoreRef.current(), [])

  // Summary cards
  const summaryCards = useMemo(() => {
    const latestPrice = klines.length > 0 ? klines[klines.length - 1]?.close : null
    const q = tencentQuote
    const items: Array<{
      label: string
      value: string
      meta: string
      icon: string
      tone?: 'default' | 'brand' | 'positive' | 'warning'
    }> = [
      {
        label: '分析标的',
        value: stockName || stockCode || '未选择',
        meta: stockName && stockCode ? `代码 ${stockCode}` : '搜索 A 股代码',
        tone: 'brand',
        icon: '⌕',
      },
      {
        label: '数据点数',
        value: klines.length ? klines.length.toLocaleString() : '—',
        meta: `${period} · ${adjust || '不复权'}`,
        icon: '◫',
      },
      {
        label: '最新价格',
        value: latestPrice != null ? `¥${latestPrice.toFixed(2)}` : '—',
        meta: q
          ? `${q.change_pct > 0 ? '+' : ''}${q.change_pct.toFixed(2)}% PE ${q.pe_ttm.toFixed(1)}`
          : '查询后展示行情',
        tone: getPriceTone(q),
        icon: '¥',
      },
      {
        label: '总成交量',
        value: volumeProfile?.total_volume
          ? volumeProfile.total_volume >= 10000
            ? `${(volumeProfile.total_volume / 10000).toFixed(0)} 万手`
            : volumeProfile.total_volume.toLocaleString()
          : '—',
        meta: volumeProfile?.poc ? `POC ¥${volumeProfile.poc.price.toFixed(2)}` : '查询后展示成交量分布',
        tone: (volumeProfile?.poc ? 'warning' : 'default') as 'warning' | 'default',
        icon: '∿',
      },
    ]
    return items
  }, [stockCode, stockName, klines, period, adjust, volumeProfile, tencentQuote])

  return (
    <AppPage>
      <PageHeader
        eyebrow="Depth Analysis"
        title="深度数据 · 量价分布与筹码分析"
        badges={
          <>
            <InfoPill>{period}</InfoPill>
            <InfoPill>K 线 + 成交量分布</InfoPill>
            <InfoPill>Volume Profile</InfoPill>
            {showCYQ && <InfoPill>筹码分布</InfoPill>}
            <InfoPill>Ctrl+K 搜索 · 1-6 切周期 · M/V/G/C 叠加</InfoPill>
          </>
        }
      />

      {/* Search + Toolbar */}
      <div className="space-y-4">
        <SurfaceCard title="股票选择与参数">
          <div className="space-y-4">
            {/* Unified search: code or name */}
            <div>
              <label className="ve-field-label">
                {stockName && stockCode ? `${stockName}（${stockCode}）` : '搜索股票'}
              </label>
              <div className="flex gap-2">
                <Input
                  id="depth-search-input"
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onPressEnter={handleUnifiedInput}
                  placeholder={stockCode || '输入代码或名称，如：贵州茅台 / 600519'}
                />
                <Button onClick={handleUnifiedInput} loading={searchLoading || loading}>
                  查询
                </Button>
                {watchlistStocks.length > 0 && (
                  <select
                    className="ve-select max-w-[140px]"
                    value=""
                    onChange={(e) => {
                      const selected = watchlistStocks.find((s) => s.code === e.target.value)
                      if (selected) handleQuickSwitch(selected.code, selected.name)
                    }}
                  >
                    <option value="" disabled>自选股</option>
                    {watchlistStocks.map((s) => (
                      <option key={s.code} value={s.code}>{s.name || s.code}</option>
                    ))}
                  </select>
                )}
              </div>

              {/* Recent symbols */}
              {recentSymbols.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">最近</span>
                  {recentSymbols.map((s) => (
                    <button
                      key={s.code}
                      type="button"
                      onClick={() => handleQuickSwitch(s.code, s.name)}
                      className="ve-info-pill cursor-pointer hover:bg-[rgba(15,118,110,0.08)] transition-colors"
                    >
                      {s.name || s.code}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {showSearchResults && (
              <div className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.78)] p-3">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-[var(--text-dim)]">候选结果</div>
                <div className="space-y-2">
                  {searchResults.length > 0 ? (
                    searchResults.map((stock) => (
                      <button
                        key={stock.code}
                        type="button"
                        onClick={() => handleSelectStock(stock)}
                        className="flex w-full items-center justify-between rounded-2xl border border-transparent bg-[rgba(248,250,252,0.88)] px-4 py-3 text-left transition hover:border-[var(--brand-line)] hover:bg-white"
                      >
                        <div>
                          <div className="font-medium text-[var(--text-strong)]">{stock.name}</div>
                          <div className="text-sm text-[var(--text-dim)]">代码 {stock.code}</div>
                        </div>
                        <div className="text-sm font-semibold text-[var(--text-strong)]">¥{stock.current_price.toFixed(2)}</div>
                      </button>
                    ))
                  ) : (
                    <div className="rounded-2xl border border-dashed border-[var(--border)] px-4 py-6 text-sm text-[var(--text-dim)]">
                      未找到相关股票
                    </div>
                  )}
                </div>
              </div>
            )}

            <DepthToolbar
              period={period}
              onPeriodChange={(p) => setPeriod(p)}
              adjust={adjust}
              onAdjustChange={setAdjust}
              showMA={showMA}
              onShowMAToggle={() => setShowMA(!showMA)}
              showVWAP={showVWAP}
              onShowVWAPToggle={() => setShowVWAP(!showVWAP)}
              showGMM={showGMM}
              onShowGMMToggle={() => setShowGMM(!showGMM)}
              showCYQ={showCYQ}
              onShowCYQToggle={() => setShowCYQ(!showCYQ)}
            />

            {error && <Alert type="error" showIcon message={error} />}
          </div>
        </SurfaceCard>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {summaryCards.map((item) => (
          <MetricCard key={item.label} {...item} />
        ))}
      </div>

      {/* Depth statistics */}
      {volumeProfile && volumeProfile.poc && volumeProfile.poc.price > 0 && (
        <DepthStatistics volumeProfile={volumeProfile} />
      )}

      {/* Main chart */}
      {klines.length > 0 ? (
        <SurfaceCard
          title={
            <div className="flex flex-wrap items-center gap-2">
              <span>{stockName || stockCode}</span>
              {stockName && stockCode && stockName !== stockCode && <InfoPill>{stockCode}</InfoPill>}
            </div>
          }
          description={`${period} K线 · Volume Profile 成交量分布`}
          actions={
            <div className="flex flex-wrap gap-2">
              <InfoPill>K线 {klines.length} 根</InfoPill>
              <InfoPill>{adjust || '不复权'}</InfoPill>
              {volumeProfile?.poc && <InfoPill>POC ¥{volumeProfile.poc.price.toFixed(2)}</InfoPill>}
            </div>
          }
        >
          <Spin spinning={loading}>
            <DepthChart
              klines={klines}
              volumeProfile={volumeProfile}
              cyqInfo={cyqInfo}
              showMA={showMA}
              showVWAP={showVWAP}
              showGMM={showGMM}
              showCYQ={showCYQ}
              hasMore={hasMore}
              loadingMore={loadingMore}
              onLoadMore={onLoadMore}
              gmmThreshold={0.7}
              currentPrice={tencentQuote?.price ?? null}
            />
          </Spin>
        </SurfaceCard>
      ) : loading ? (
        <SurfaceCard title="深度数据图表" description="正在加载数据...">
          <div className="animate-pulse space-y-3">
            <div className="h-[420px] rounded-2xl bg-slate-200/70" />
            <div className="flex gap-3">
              <div className="h-10 w-24 rounded-xl bg-slate-200/70" />
              <div className="h-10 w-20 rounded-xl bg-slate-200/70" />
              <div className="h-10 w-28 rounded-xl bg-slate-200/70" />
            </div>
          </div>
        </SurfaceCard>
      ) : (
        <SurfaceCard title="深度数据图表" description="查询完成后，这里会出现多周期 K 线 + 成交量分布叠加视图。">
          <EmptyState title="还没有深度数据" description="输入股票代码并选择周期后执行查询，图表自动加载最大可用数据范围。" />
        </SurfaceCard>
      )}

      {/* CYQ info cards */}
      {cyqInfo && showCYQ && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <MetricCard
            label="平均成本"
            value={`¥${cyqInfo.avg_cost.toFixed(2)}`}
            meta={<span className={marketClassByValue(cyqInfo.profit_ratio)}>获利比例 {formatPct(cyqInfo.profit_ratio)}</span>}
            tone="brand"
            icon="¥"
          />
          <MetricCard
            label="90% 成本区"
            value={`¥${cyqInfo.cost_90_low.toFixed(2)} - ¥${cyqInfo.cost_90_high.toFixed(2)}`}
            meta={`集中度 ${(cyqInfo.concentration_90 * 100).toFixed(2)}%`}
            icon="▥"
          />
          <MetricCard
            label="70% 成本区"
            value={`¥${cyqInfo.cost_70_low.toFixed(2)} - ¥${cyqInfo.cost_70_high.toFixed(2)}`}
            meta={`集中度 ${(cyqInfo.concentration_70 * 100).toFixed(2)}%`}
            tone="warning"
            icon="◫"
          />
        </div>
      )}

      {/* Stock info: Tencent + Eastmoney */}
      {(tencentQuote || stockInfo) && (
        <SurfaceCard title="个股信息" description={[tencentQuote ? '腾讯行情' : '', stockInfo?.industry ? `行业: ${stockInfo.industry}` : ''].filter(Boolean).join(' · ')}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
            {tencentQuote && (
              <>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">最新价</div>
                  <div className="text-lg font-semibold text-[var(--text-strong)]">¥{tencentQuote.price.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">涨跌幅</div>
                  <div className={marketClassByValue(tencentQuote.change_pct)}>
                    {tencentQuote.change_pct > 0 ? '+' : ''}{tencentQuote.change_pct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">PE (TTM)</div>
                  <div className="text-sm text-[var(--text-strong)]">{tencentQuote.pe_ttm.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">PB</div>
                  <div className="text-sm text-[var(--text-strong)]">{tencentQuote.pb.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">涨停 / 跌停</div>
                  <div className="text-sm">
                    <span className="text-red-600">{tencentQuote.limit_up.toFixed(2)}</span>
                    {' / '}
                    <span className="text-green-600">{tencentQuote.limit_down.toFixed(2)}</span>
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">开盘 / 最高 / 最低</div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {tencentQuote.open.toFixed(2)} / {tencentQuote.high.toFixed(2)} / {tencentQuote.low.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">换手率 / 量比</div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {tencentQuote.turnover_pct.toFixed(2)}% / {tencentQuote.vol_ratio.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">总市值</div>
                  <div className="text-xs text-[var(--text-strong)]">{tencentQuote.mcap_yi.toFixed(0)} 亿</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">流通市值</div>
                  <div className="text-xs text-[var(--text-strong)]">{tencentQuote.float_mcap_yi.toFixed(0)} 亿</div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">成交额</div>
                  <div className="text-xs text-[var(--text-strong)]">{tencentQuote.amount_wan.toFixed(0)} 万</div>
                </div>
              </>
            )}
            {stockInfo && (
              <>
                {stockInfo.industry && (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">所属行业</div>
                    <div className="text-xs text-[var(--text-strong)]">{stockInfo.industry}</div>
                  </div>
                )}
                {stockInfo.list_date && (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">上市日期</div>
                    <div className="text-xs text-[var(--text-muted)]">{stockInfo.list_date}</div>
                  </div>
                )}
                {stockInfo.total_shares > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">总股本 / 流通股本</div>
                    <div className="text-xs text-[var(--text-muted)]">
                      {(stockInfo.total_shares / 100000000).toFixed(2)} 亿 / {(stockInfo.float_shares / 100000000).toFixed(2)} 亿
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </SurfaceCard>
      )}
    </AppPage>
  )
}
