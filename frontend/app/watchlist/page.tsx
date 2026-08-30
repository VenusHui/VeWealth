'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import axios from 'axios'
import {
  Alert,
  Button,
  Input,
  InputNumber,
  Popconfirm,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getAuthHeader, getUser, isAuthenticated } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'
import { AppPage, CompactStatCard, EmptyState, SurfaceCard } from '../components/ui-shell'
import { marketClassByValue, formatPct } from '../lib/marketColors'

const API_BASE_URL = getApiBaseUrl()

interface WatchListItem {
  id: number
  stock_code: string
  stock_name?: string
  alert_enabled: boolean
  alert_threshold?: number
  last_alerted_at?: string
  created_at: string
  updated_at: string
  current_price?: number | null
  change_pct?: number | null
  change_amt?: number | null
  gmm_signal?: string | null
  gmm_density?: number | null
  gmm_peak_price?: number | null
}

export default function WatchListPage() {
  const router = useRouter()
  const [watchlist, setWatchlist] = useState<WatchListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [alertEnabled, setAlertEnabled] = useState(true)
  const [alertThreshold, setAlertThreshold] = useState<number>(0.7)
  const [addLoading, setAddLoading] = useState(false)
  const [userThreshold, setUserThreshold] = useState(0.7)

  const fetchWatchList = useCallback(async () => {
    try {
      setLoading(true)
      setError('')
      const response = await axios.get(`${API_BASE_URL}/api/watchlist`, {
        headers: getAuthHeader(),
      })
      if (response.data.success) {
        setWatchlist(response.data.data)
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '获取监控列表失败'
      if (err.response?.status === 401) {
        router.push('/login')
      } else {
        setError(errorMsg)
      }
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push('/login')
      return
    }
    const user = getUser()
    if (user) {
      setUserThreshold(user.alert_threshold)
      setAlertThreshold(user.alert_threshold)
    }
    fetchWatchList()
  }, [fetchWatchList, router])

  // Auto-poll quotes every 30s
  useEffect(() => {
    if (!isAuthenticated()) return
    const timer = setInterval(() => fetchWatchList(), 30000)
    return () => clearInterval(timer)
  }, [fetchWatchList])

  const handleAddStock = async () => {
    if (!stockCode.trim()) {
      message.warning('请输入股票代码')
      return
    }
    if (!/^\d{6}$/.test(stockCode.trim())) {
      message.warning('股票代码格式错误，应为 6 位数字')
      return
    }
    try {
      setAddLoading(true)
      const response = await axios.post(
        `${API_BASE_URL}/api/watchlist`,
        {
          stock_code: stockCode.trim(),
          stock_name: stockName.trim() || null,
          alert_enabled: alertEnabled,
          alert_threshold: alertThreshold || null,
        },
        { headers: getAuthHeader() },
      )
      if (response.data.success) {
        setStockCode('')
        setStockName('')
        setAlertEnabled(true)
        setAlertThreshold(userThreshold || 0.7)
        setShowAddForm(false)
        message.success('添加成功')
        fetchWatchList()
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '添加失败')
    } finally {
      setAddLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await axios.delete(`${API_BASE_URL}/api/watchlist/${id}`, {
        headers: getAuthHeader(),
      })
      message.success('删除成功')
      fetchWatchList()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败')
    }
  }

  const handleToggleAlert = async (item: WatchListItem) => {
    try {
      await axios.put(
        `${API_BASE_URL}/api/watchlist/${item.id}`,
        { alert_enabled: !item.alert_enabled },
        { headers: getAuthHeader() },
      )
      fetchWatchList()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败')
    }
  }

  const columns: ColumnsType<WatchListItem> = [
    { title: '代码', dataIndex: 'stock_code', key: 'stock_code', width: 100 },
    {
      title: '名称',
      dataIndex: 'stock_name',
      key: 'stock_name',
      width: 120,
      render: (v) => v || '-',
    },
    {
      title: '最新价',
      dataIndex: 'current_price',
      key: 'current_price',
      width: 90,
      align: 'right',
      render: (v) => (v != null ? `¥${v.toFixed(2)}` : '-'),
    },
    {
      title: '涨跌幅',
      dataIndex: 'change_pct',
      key: 'change_pct',
      width: 90,
      align: 'right',
      sorter: (a, b) => (a.change_pct ?? 0) - (b.change_pct ?? 0),
      render: (v) =>
        v != null ? <span className={marketClassByValue(v)}>{v > 0 ? '+' : ''}{v.toFixed(2)}%</span> : '-',
    },
    {
      title: '涨跌额',
      dataIndex: 'change_amt',
      key: 'change_amt',
      width: 80,
      align: 'right',
      responsive: ['lg'],
      render: (v) =>
        v != null ? <span className={marketClassByValue(v)}>{v > 0 ? '+' : ''}{v.toFixed(2)}</span> : '-',
    },
    {
      title: '预警',
      dataIndex: 'alert_enabled',
      key: 'alert_enabled',
      width: 80,
      render: (_, row) => <Switch size="small" checked={row.alert_enabled} onChange={() => handleToggleAlert(row)} />,
    },
    {
      title: '信号',
      dataIndex: 'gmm_signal',
      key: 'gmm_signal',
      width: 80,
      render: (_v, row) => {
        if (!row.alert_enabled) return <Tag>关闭</Tag>
        if (!row.gmm_signal) return <Tag>—</Tag>
        const densityPct = row.gmm_density != null ? `${(row.gmm_density * 100).toFixed(0)}%` : ''
        if (row.gmm_signal === 'buy') return <Tag color="red">买入 {densityPct}</Tag>
        if (row.gmm_signal === 'sell') return <Tag color="green">卖出 {densityPct}</Tag>
        return <Tag>{densityPct || '中性'}</Tag>
      },
    },
    {
      title: '阈值',
      dataIndex: 'alert_threshold',
      key: 'alert_threshold',
      width: 70,
      align: 'right',
      responsive: ['lg'],
      render: (v) => {
        if (v == null) return '-'
        const upper = Number(v)
        const lower = 1 - upper
        return (
          <Tooltip title={`卖出信号: 密度 ≥ ${(upper * 100).toFixed(0)}% | 买入信号: 密度 ≤ ${(lower * 100).toFixed(0)}%`}>
            <span className="cursor-help border-b border-dotted border-[var(--text-dim)]">{(upper * 100).toFixed(0)}%</span>
          </Tooltip>
        )
      },
    },
    {
      title: '最近预警',
      dataIndex: 'last_alerted_at',
      key: 'last_alerted_at',
      width: 160,
      responsive: ['lg'],
      render: (v) => (v ? new Date(v).toLocaleString() : <Tag>未触发</Tag>),
    },
    {
      title: '操作',
      key: 'action',
      width: 130,
      render: (_, row) => (
        <div className="flex items-center gap-1">
          <Link href={`/depth?code=${row.stock_code}`} className="ve-tab-button text-xs px-2 py-1">分析</Link>
          <Popconfirm title={`确定要删除 ${row.stock_code} 吗？`} onConfirm={() => handleDelete(row.id)}>
            <Button danger type="link" size="small">删除</Button>
          </Popconfirm>
        </div>
      ),
    },
  ]

  const enabledCount = useMemo(() => watchlist.filter((item) => item.alert_enabled).length, [watchlist])
  const triggeredCount = useMemo(() => watchlist.filter((item) => item.last_alerted_at).length, [watchlist])
  const upCount = useMemo(() => watchlist.filter((item) => (item.change_pct ?? 0) > 0).length, [watchlist])
  const downCount = useMemo(() => watchlist.filter((item) => (item.change_pct ?? 0) < 0).length, [watchlist])
  const flatCount = useMemo(() => watchlist.filter((item) => (item.change_pct ?? 0) === 0).length, [watchlist])
  const buySignalCount = useMemo(() => watchlist.filter((item) => item.gmm_signal === 'buy').length, [watchlist])
  const sellSignalCount = useMemo(() => watchlist.filter((item) => item.gmm_signal === 'sell').length, [watchlist])

  return (
    <AppPage>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px_1fr]">
        {/* Left sidebar: add form + compact stats */}
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2">
            <CompactStatCard label="监控" value={watchlist.length} tone="brand" />
            <CompactStatCard label="↑涨 ↓跌" value={`${upCount}/${downCount}`} />
            <CompactStatCard label="预警中" value={enabledCount} tone="brand" />
            <CompactStatCard label="信号" value={`${buySignalCount}/${sellSignalCount}`} />
          </div>

          <SurfaceCard title="添加监控" description="输入代码和预警阈值">
            {showAddForm ? (
              <div className="space-y-4">
                <div>
                  <label htmlFor="watch-stock-code" className="ve-field-label">股票代码</label>
                  <Input id="watch-stock-code" value={stockCode} onChange={(e) => setStockCode(e.target.value)} placeholder="如：000001" maxLength={6} />
                </div>
                <div>
                  <label htmlFor="watch-stock-name" className="ve-field-label">股票名称（可选）</label>
                  <Input id="watch-stock-name" value={stockName} onChange={(e) => setStockName(e.target.value)} placeholder="如：平安银行" />
                </div>
                <div>
                  <label className="ve-field-label">预警阈值</label>
                  <InputNumber
                    min={0.1} max={1} step={0.05} precision={2}
                    value={alertThreshold}
                    onChange={(v) => setAlertThreshold(Number(v || 0.7))}
                    style={{ width: '100%' }}
                  />
                  <div className="text-xs text-[var(--text-dim)] mt-1">
                    卖出≥{(alertThreshold * 100).toFixed(0)}% · 买入≤{((1 - alertThreshold) * 100).toFixed(0)}%
                  </div>
                </div>
                <div>
                  <label className="ve-field-label">启用预警</label>
                  <Switch checked={alertEnabled} onChange={setAlertEnabled} />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="primary" loading={addLoading} onClick={handleAddStock} block>添加</Button>
                  <Button onClick={() => setShowAddForm(false)} block>取消</Button>
                </div>
              </div>
            ) : (
              <EmptyState title="添加面板已折叠" description="展开后可输入股票代码并设置预警阈值。" action={<button type="button" className="ve-button-secondary text-xs" onClick={() => setShowAddForm(true)}>展开</button>} />
            )}
          </SurfaceCard>

          {error ? <Alert type="error" message={error} /> : null}
        </div>

        {/* Right: watchlist table */}
        <div className="min-w-0">
          <SurfaceCard title="监控列表">
            {loading ? (
              <div className="py-16 text-center"><Spin /></div>
            ) : watchlist.length > 0 ? (
              <>
                <div className="hidden md:block">
                  <Table<WatchListItem>
                    rowKey="id"
                    size="small"
                    columns={columns}
                    dataSource={watchlist}
                    pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
                    scroll={{ x: 1000, y: 480 }}
                  />
                </div>

                <div className="space-y-3 md:hidden">
                  {watchlist.map((item) => (
                    <div key={item.id} className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="font-semibold text-[var(--text-strong)]">{item.stock_name || item.stock_code}</div>
                          <div className="text-sm text-[var(--text-dim)]">{item.stock_code}</div>
                        </div>
                        <div className="text-right">
                          {item.current_price != null ? (
                            <>
                              <div className="font-semibold text-[var(--text-strong)]">¥{item.current_price.toFixed(2)}</div>
                              {item.change_pct != null && (
                                <div className={marketClassByValue(item.change_pct)}>
                                  {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                                </div>
                              )}
                            </>
                          ) : (
                            <Tag>无行情</Tag>
                          )}
                        </div>
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                        <div>
                          <div className="text-[var(--text-dim)]">GMM 信号</div>
                          <div className="font-medium text-[var(--text-strong)]">
                            {!item.alert_enabled ? <Tag>关闭</Tag> :
                             item.gmm_signal === 'buy' ? <Tag color="red">买入 {(item.gmm_density != null ? (item.gmm_density * 100).toFixed(0) : '')}%</Tag> :
                             item.gmm_signal === 'sell' ? <Tag color="green">卖出 {(item.gmm_density != null ? (item.gmm_density * 100).toFixed(0) : '')}%</Tag> :
                             item.gmm_signal === 'neutral' ? <Tag>中性</Tag> :
                             <span className="text-[var(--text-dim)]">—</span>}
                          </div>
                        </div>
                        <div>
                          <div className="text-[var(--text-dim)]">阈值 / 峰值</div>
                          <div className="font-medium text-[var(--text-strong)]">
                            {item.alert_threshold != null ? `${(Number(item.alert_threshold) * 100).toFixed(0)}%` : '-'}
                            {item.gmm_peak_price != null ? ` / ¥${item.gmm_peak_price.toFixed(2)}` : ''}
                          </div>
                        </div>
                      </div>
                      <div className="mt-4 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Switch size="small" checked={item.alert_enabled} onChange={() => handleToggleAlert(item)} />
                          <Link href={`/depth?code=${item.stock_code}`} className="ve-tab-button text-xs px-2 py-1">分析</Link>
                        </div>
                        <Popconfirm title={`确定要删除 ${item.stock_code} 吗？`} onConfirm={() => handleDelete(item.id)}>
                          <Button danger type="link" size="small" className="p-0">删除</Button>
                        </Popconfirm>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <EmptyState title="还没有监控股票" description="先添加你关注的标的，再按阈值管理告警和最近触发状态。" action={<button type="button" className="ve-button-primary" onClick={() => setShowAddForm(true)}>添加第一只股票</button>} />
            )}
          </SurfaceCard>
        </div>
      </div>
    </AppPage>
  )
}
