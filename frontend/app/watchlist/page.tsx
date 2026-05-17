'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useRouter } from 'next/navigation'
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
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getAuthHeader, getUser, isAuthenticated } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'
import { AppPage, EmptyState, InfoPill, MetricCard, PageHeader, SurfaceCard } from '../components/ui-shell'

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
    { title: '股票代码', dataIndex: 'stock_code', key: 'stock_code', width: 120 },
    {
      title: '股票名称',
      dataIndex: 'stock_name',
      key: 'stock_name',
      width: 140,
      render: (v) => v || '-',
    },
    {
      title: '预警开关',
      dataIndex: 'alert_enabled',
      key: 'alert_enabled',
      width: 120,
      render: (_, row) => <Switch checked={row.alert_enabled} onChange={() => handleToggleAlert(row)} />,
    },
    {
      title: '阈值',
      dataIndex: 'alert_threshold',
      key: 'alert_threshold',
      width: 100,
      align: 'right',
      render: (v) => (v != null ? `${(Number(v) * 100).toFixed(0)}%` : '-'),
    },
    {
      title: '最近预警',
      dataIndex: 'last_alerted_at',
      key: 'last_alerted_at',
      width: 170,
      render: (v) => (v ? new Date(v).toLocaleString() : <Tag>未触发</Tag>),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v) => new Date(v).toLocaleString(),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, row) => (
        <Popconfirm title={`确定要删除 ${row.stock_code} 吗？`} onConfirm={() => handleDelete(row.id)}>
          <Button danger type="link">删除</Button>
        </Popconfirm>
      ),
    },
  ]

  const enabledCount = useMemo(() => watchlist.filter((item) => item.alert_enabled).length, [watchlist])
  const triggeredCount = useMemo(() => watchlist.filter((item) => item.last_alerted_at).length, [watchlist])

  return (
    <AppPage>
      <PageHeader
        eyebrow="Watchlist"
        title="监控列表"
        badges={(
          <>
            <InfoPill>默认阈值 {(userThreshold * 100).toFixed(0)}%</InfoPill>
            <InfoPill>支持单股单独阈值</InfoPill>
          </>
        )}
        actions={(
          <button type="button" className="ve-button-primary" onClick={() => setShowAddForm((v) => !v)}>
            {showAddForm ? '收起添加面板' : '添加监控股票'}
          </button>
        )}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard label="监控股票" value={watchlist.length.toLocaleString()} meta="当前监控池" tone="brand" icon="◌" />
        <MetricCard label="启用预警" value={enabledCount.toLocaleString()} meta="启用中的标的" icon="⦿" />
        <MetricCard label="历史触发" value={triggeredCount.toLocaleString()} meta="触发过预警的标的" tone="warning" icon="!" />
      </div>

      <div className="grid grid-cols-1 gap-4">
        <SurfaceCard title="添加监控" description="添加新股票并为该标的设置预警阈值。">
          <div className="space-y-4">
            {showAddForm ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <label htmlFor="watch-stock-code" className="ve-field-label">股票代码</label>
                  <Input id="watch-stock-code" value={stockCode} onChange={(e) => setStockCode(e.target.value)} placeholder="如：000001" maxLength={6} />
                </div>
                <div>
                  <label htmlFor="watch-stock-name" className="ve-field-label">股票名称（可选）</label>
                  <Input id="watch-stock-name" value={stockName} onChange={(e) => setStockName(e.target.value)} placeholder="如：平安银行" />
                </div>
                <div className="space-y-2">
                  <label className="ve-field-label">启用预警</label>
                  <div className="flex h-[42px] items-center rounded-2xl border border-[var(--border)] bg-[rgba(255,255,255,0.88)] px-4">
                    <Switch checked={alertEnabled} onChange={setAlertEnabled} />
                  </div>
                </div>
                <div>
                  <label className="ve-field-label">预警阈值</label>
                  <InputNumber
                    min={0.1}
                    max={1}
                    step={0.05}
                    precision={2}
                    value={alertThreshold}
                    onChange={(v) => setAlertThreshold(Number(v || 0.7))}
                    style={{ width: '100%' }}
                  />
                </div>
                <div className="md:col-span-2 flex flex-wrap gap-2">
                  <Button type="primary" loading={addLoading} onClick={handleAddStock}>添加到监控列表</Button>
                  <Button onClick={() => setShowAddForm(false)}>取消</Button>
                </div>
              </div>
            ) : (
              <EmptyState title="添加面板已折叠" description="展开后可输入股票代码并设置预警阈值。" action={<button type="button" className="ve-button-secondary" onClick={() => setShowAddForm(true)}>展开添加面板</button>} />
            )}
          </div>
        </SurfaceCard>
      </div>

      {error ? <Alert type="error" message={error} /> : null}

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
                scroll={{ x: 1000 }}
              />
            </div>

            <div className="space-y-3 md:hidden">
              {watchlist.map((item) => (
                <div key={item.id} className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.75)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-[var(--text-strong)]">{item.stock_name || item.stock_code}</div>
                      <div className="text-sm text-[var(--text-dim)]">{item.stock_code}</div>
                    </div>
                    <Tag>{item.alert_enabled ? '启用中' : '已关闭'}</Tag>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <div className="text-[var(--text-dim)]">阈值</div>
                      <div className="font-medium text-[var(--text-strong)]">{item.alert_threshold != null ? `${(Number(item.alert_threshold) * 100).toFixed(0)}%` : '-'}</div>
                    </div>
                    <div>
                      <div className="text-[var(--text-dim)]">最近预警</div>
                      <div className="font-medium text-[var(--text-strong)]">{item.last_alerted_at ? new Date(item.last_alerted_at).toLocaleString() : '未触发'}</div>
                    </div>
                  </div>
                  <div className="mt-4 flex items-center justify-between">
                    <Switch checked={item.alert_enabled} onChange={() => handleToggleAlert(item)} />
                    <Popconfirm title={`确定要删除 ${item.stock_code} 吗？`} onConfirm={() => handleDelete(item.id)}>
                      <Button danger type="link" className="p-0">删除</Button>
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
    </AppPage>
  )
}
