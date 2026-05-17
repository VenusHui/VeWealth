'use client'

import { useMemo, useState, useEffect } from 'react'
import axios from 'axios'
import { format } from 'date-fns'
import { Alert, Button, DatePicker, Input, Spin } from 'antd'
import dayjs from 'dayjs'
import StockChart from '../components/StockChart'
import { AppPage, EmptyState, InfoPill, MetricCard, PageHeader, SurfaceCard } from '../components/ui-shell'
import { formatPct, marketClassByValue } from '../lib/marketColors'
import { getApiBaseUrl } from '../lib/api'

interface StockSearchResult {
  code: string
  name: string
  current_price: number
}

interface ChartDataPoint {
  datetime: string
  price: number
  volume: number
  open?: number
  high?: number
  low?: number
}

interface GaussianComponent {
  mean: number
  std: number
  weight: number
  volume: number
}

interface FitCurvePoint {
  price: number
  fitVolume: number
}

interface FitResult {
  n_components: number
  components: GaussianComponent[]
  fit_curve: FitCurvePoint[]
  bic: number
}

interface CyqInfo {
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

const API_BASE_URL = getApiBaseUrl()

function formatCompactNumber(value: number): string {
  if (value >= 100000000) return `${(value / 100000000).toFixed(2)} 亿`
  if (value >= 10000) return `${(value / 10000).toFixed(0)} 万`
  return value.toLocaleString()
}

export default function AnalysisPage() {
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [chartData, setChartData] = useState<ChartDataPoint[]>([])
  const [fitResult, setFitResult] = useState<FitResult | null>(null)
  const [cyqInfo, setCyqInfo] = useState<CyqInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [actualStartDate, setActualStartDate] = useState('')
  const [actualEndDate, setActualEndDate] = useState('')

  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [showSearchResults, setShowSearchResults] = useState(false)

  useEffect(() => {
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - 1)
    setEndDate(format(end, 'yyyy-MM-dd'))
    setStartDate(format(start, 'yyyy-MM-dd'))
  }, [])

  const handleSearch = async () => {
    if (!searchKeyword.trim()) {
      setSearchResults([])
      setShowSearchResults(false)
      return
    }

    try {
      setSearchLoading(true)
      const response = await axios.get(`${API_BASE_URL}/api/stock/search`, {
        params: { keyword: searchKeyword.trim() },
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
  }

  const handleFetchData = async () => {
    if (!stockCode.trim()) {
      setError('请输入股票代码')
      return
    }
    if (!/^\d{6}$/.test(stockCode.trim())) {
      setError('股票代码格式错误，应为 6 位数字（如：000001）')
      return
    }
    if (!startDate || !endDate) {
      setError('请选择日期范围')
      return
    }

    try {
      setLoading(true)
      setError('')
      const hasStockName = !!stockName

      const [stockDataResponse, cyqDataResponse] = await Promise.allSettled([
        axios.get(`${API_BASE_URL}/api/stock/data`, {
          params: {
            symbol: stockCode.trim(),
            start_date: startDate,
            end_date: endDate,
          },
        }),
        axios.get(`${API_BASE_URL}/api/stock/cyq`, {
          params: {
            symbol: stockCode.trim(),
            adjust: '',
          },
        }),
      ])

      if (stockDataResponse.status === 'fulfilled' && stockDataResponse.value.data.success) {
        setChartData(stockDataResponse.value.data.chart_data)
        setFitResult(stockDataResponse.value.data.fit_result || null)
        setActualStartDate(stockDataResponse.value.data.actual_start_date || startDate)
        setActualEndDate(stockDataResponse.value.data.actual_end_date || endDate)
        if (!hasStockName) {
          setStockName(stockCode.trim())
        }
      } else {
        const errorMsg =
          stockDataResponse.status === 'rejected'
            ? stockDataResponse.reason?.response?.data?.detail || '获取股票数据失败'
            : '获取股票数据失败'

        if (String(errorMsg).includes('未找到')) {
          setError(`股票代码 ${stockCode} 不存在或数据不可用，请检查代码是否正确`)
        } else {
          setError(errorMsg)
        }
        setChartData([])
        setFitResult(null)
        setActualStartDate('')
        setActualEndDate('')
      }

      if (cyqDataResponse.status === 'fulfilled' && cyqDataResponse.value.data.success) {
        setCyqInfo(cyqDataResponse.value.data.cyq_info)
      } else {
        setCyqInfo(null)
      }
    } catch (err: unknown) {
      const errorMsg =
        typeof err === 'object' && err !== null && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || '获取数据失败'
          : '获取数据失败'
      setError(errorMsg)
      setChartData([])
      setFitResult(null)
      setCyqInfo(null)
      setActualStartDate('')
      setActualEndDate('')
    } finally {
      setLoading(false)
    }
  }

  const totalVolume = useMemo(
    () => chartData.reduce((sum, item) => sum + Number(item.volume || 0), 0),
    [chartData],
  )

  const latestPrice = useMemo(() => {
    if (!chartData.length) return null
    return chartData[chartData.length - 1]?.price ?? null
  }, [chartData])

  const summaryCards = useMemo<Array<{
    label: string
    value: string
    meta: string
    tone?: 'default' | 'brand' | 'positive' | 'warning'
    icon: string
  }>>(
    () => [
      {
        label: '分析标的',
        value: stockName || stockCode || '未选择',
        meta: stockName && stockCode && stockName !== stockCode ? `代码 ${stockCode}` : '搜索或输入 6 位股票代码',
        tone: 'brand' as const,
        icon: '⌕',
      },
      {
        label: '数据点数',
        value: chartData.length ? chartData.length.toLocaleString() : '—',
        meta: actualStartDate && actualEndDate ? `实际范围 ${actualStartDate} → ${actualEndDate}` : '等待查询结果',
        icon: '◫',
      },
      {
        label: '最新价格',
        value: latestPrice != null ? `¥${Number(latestPrice).toFixed(2)}` : '—',
        meta: cyqInfo ? `获利比例 ${formatPct(cyqInfo.profit_ratio)}` : '支持筹码信息联动查看',
        tone: latestPrice != null ? 'positive' : 'default',
        icon: '¥',
      },
      {
        label: '成交量汇总',
        value: chartData.length ? formatCompactNumber(totalVolume) : '—',
        meta: fitResult ? `${fitResult.n_components} 个高斯分量` : '查询后展示拟合结果',
        tone: fitResult ? 'warning' : 'default',
        icon: '∿',
      },
    ],
    [actualEndDate, actualStartDate, chartData.length, cyqInfo, fitResult, latestPrice, stockCode, stockName, totalVolume],
  )

  return (
    <AppPage>
      <PageHeader
        eyebrow="Analysis"
        title="价格分布与筹码结构分析"
        badges={(
          <>
            <InfoPill>1 分钟粒度</InfoPill>
            <InfoPill>价格分布</InfoPill>
            <InfoPill>GMM 拟合</InfoPill>
            <InfoPill>筹码分布</InfoPill>
          </>
        )}
      />

      <div className="grid grid-cols-1 gap-4">
        <SurfaceCard title="分析条件">
          <div className="space-y-4">
            <div>
              <label htmlFor="stock-search" className="ve-field-label">搜索股票</label>
              <div className="flex gap-2">
                <Input
                  id="stock-search"
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onPressEnter={handleSearch}
                  placeholder="输入股票代码或名称搜索，如：平安银行 / 000001"
                />
                <Button onClick={handleSearch} loading={searchLoading} disabled={!searchKeyword.trim()}>
                  搜索
                </Button>
              </div>
            </div>

            {showSearchResults ? (
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
            ) : null}

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div>
                <label htmlFor="stock-code" className="ve-field-label">{stockName ? `股票代码（${stockName}）` : '股票代码'}</label>
                <Input
                  id="stock-code"
                  value={stockCode}
                  onChange={(e) => setStockCode(e.target.value)}
                  onPressEnter={handleFetchData}
                  maxLength={6}
                  placeholder="输入 6 位股票代码"
                />
              </div>
              <div>
                <label className="ve-field-label">开始日期</label>
                <DatePicker
                  value={startDate ? dayjs(startDate) : null}
                  onChange={(d) => setStartDate(d ? d.format('YYYY-MM-DD') : '')}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label className="ve-field-label">结束日期</label>
                <DatePicker
                  value={endDate ? dayjs(endDate) : null}
                  onChange={(d) => setEndDate(d ? d.format('YYYY-MM-DD') : '')}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button type="primary" onClick={handleFetchData} loading={loading}>
                查询股票数据
              </Button>
              <InfoPill>请求范围 {startDate || '—'} → {endDate || '—'}</InfoPill>
            </div>

            {error ? <Alert type="error" showIcon message={error} /> : null}
          </div>
        </SurfaceCard>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {summaryCards.map((item) => (
          <MetricCard key={item.label} {...item} />
        ))}
      </div>

      {chartData.length > 0 ? (
        <SurfaceCard
          title={
            <div className="flex flex-wrap items-center gap-2">
              <span>{stockName || stockCode}</span>
              {stockName && stockCode !== stockName ? <InfoPill>{stockCode}</InfoPill> : null}
            </div>
          }
          description="价格分布 · 拟合曲线 · 筹码分布"
          actions={(
            <div className="flex flex-wrap gap-2">
              <InfoPill>数据点数 {chartData.length}</InfoPill>
              <InfoPill>粒度 1 分钟</InfoPill>
              {actualStartDate && actualEndDate ? <InfoPill>实际范围 {actualStartDate} → {actualEndDate}</InfoPill> : null}
            </div>
          )}
        >
          <Spin spinning={loading}>
            <StockChart data={chartData} period="1min" fitResult={fitResult} cyqInfo={cyqInfo} />
          </Spin>
        </SurfaceCard>
      ) : (
        <SurfaceCard title="图表工作区" description="查询完成后，这里会出现价格分布图、拟合曲线和筹码信息。">
          <EmptyState title="还没有分析结果" description="输入股票代码并选择日期范围后执行查询，即可进入图表分析。" />
        </SurfaceCard>
      )}

      {cyqInfo ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <MetricCard label="平均成本" value={`¥${cyqInfo.avg_cost.toFixed(2)}`} meta={<span className={marketClassByValue(cyqInfo.profit_ratio)}>获利比例 {formatPct(cyqInfo.profit_ratio)}</span>} tone="brand" icon="¥" />
          <MetricCard label="90% 成本区" value={`¥${cyqInfo.cost_90_low.toFixed(2)} - ¥${cyqInfo.cost_90_high.toFixed(2)}`} meta={`集中度 ${(cyqInfo.concentration_90 * 100).toFixed(2)}%`} icon="▥" />
          <MetricCard label="70% 成本区" value={`¥${cyqInfo.cost_70_low.toFixed(2)} - ¥${cyqInfo.cost_70_high.toFixed(2)}`} meta={`集中度 ${(cyqInfo.concentration_70 * 100).toFixed(2)}%`} tone="warning" icon="◫" />
        </div>
      ) : null}
    </AppPage>
  )
}
