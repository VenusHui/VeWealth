'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import axios from 'axios'
import { Spin, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getAuthHeader, isAuthenticated } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'
import { marketClassByValue } from '../lib/marketColors'
import { AppPage, EmptyState, InfoPill, MetricCard, PageHeader, SurfaceCard } from '../components/ui-shell'

const API_BASE_URL = getApiBaseUrl()

interface AlertItem {
  id: number
  user_id: number
  stock_code: string
  stock_name?: string
  alert_threshold?: number | null
  current_price: number
  change_pct?: number | null
  created_at: string
}

export default function AlertsPage() {
  const router = useRouter()
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const fetchAlerts = useCallback(async (p = page, ps = pageSize) => {
    if (!isAuthenticated()) {
      router.push('/login')
      return
    }
    try {
      setLoading(true)
      const offset = (p - 1) * ps
      const resp = await axios.get(`${API_BASE_URL}/api/alerts?limit=${ps}&offset=${offset}`, {
        headers: getAuthHeader(),
      })
      if (resp.data?.success) {
        setAlerts(resp.data.data || [])
        setTotal(resp.data.total || 0)
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, router])

  useEffect(() => {
    fetchAlerts()
  }, [fetchAlerts])

  const columns: ColumnsType<AlertItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v) => new Date(v).toLocaleString(),
    },
    { title: '代码', dataIndex: 'stock_code', key: 'stock_code', width: 100 },
    {
      title: '名称',
      dataIndex: 'stock_name',
      key: 'stock_name',
      width: 120,
      render: (v) => v || '-',
    },
    {
      title: '触发价',
      dataIndex: 'current_price',
      key: 'current_price',
      width: 100,
      align: 'right',
      render: (v) => `¥${v.toFixed(2)}`,
    },
    {
      title: '涨跌幅',
      dataIndex: 'change_pct',
      key: 'change_pct',
      width: 90,
      align: 'right',
      render: (v) =>
        v != null ? <span className={marketClassByValue(v)}>{v > 0 ? '+' : ''}{v.toFixed(2)}%</span> : '-',
    },
    {
      title: '阈值',
      dataIndex: 'alert_threshold',
      key: 'alert_threshold',
      width: 80,
      align: 'right',
      render: (v) => (v != null ? `${(Number(v) * 100).toFixed(0)}%` : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, row) => (
        <Link href={`/depth?code=${row.stock_code}`} className="ve-tab-button text-xs px-2 py-1">
          分析
        </Link>
      ),
    },
  ]

  const todayCount = alerts.filter((a) => {
    const d = new Date(a.created_at)
    const now = new Date()
    return d.toDateString() === now.toDateString()
  }).length

  return (
    <AppPage>
      <PageHeader
        eyebrow="Alert History"
        title="预警历史"
        badges={
          <>
            <InfoPill>共 {total} 条记录</InfoPill>
            <InfoPill>今日 {todayCount} 条</InfoPill>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard label="总预警" value={total.toLocaleString()} meta="历史累计" tone="brand" icon="!" />
        <MetricCard label="今日" value={todayCount.toLocaleString()} meta="今日触发" tone="warning" icon="⦿" />
        <MetricCard label="涉及标的" value={[...new Set(alerts.map((a) => a.stock_code))].length.toLocaleString()} meta="不同股票" icon="◌" />
      </div>

      <SurfaceCard title="预警记录">
        {loading ? (
          <div className="py-16 text-center"><Spin /></div>
        ) : alerts.length > 0 ? (
          <>
            <div className="hidden md:block">
              <Table<AlertItem>
                rowKey="id"
                size="small"
                columns={columns}
                dataSource={alerts}
                pagination={{
                  current: page,
                  pageSize,
                  total,
                  showSizeChanger: true,
                  pageSizeOptions: [20, 50, 100],
                  onChange: (p, ps) => {
                    setPage(p)
                    setPageSize(ps)
                  },
                }}
                scroll={{ x: 800 }}
              />
            </div>

            <div className="space-y-3 md:hidden">
              {alerts.map((item) => (
                <div key={item.id} className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.75)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-[var(--text-strong)]">{item.stock_name || item.stock_code}</div>
                      <div className="text-sm text-[var(--text-dim)]">{item.stock_code}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-semibold text-[var(--text-strong)]">¥{item.current_price.toFixed(2)}</div>
                      {item.change_pct != null && (
                        <div className={marketClassByValue(item.change_pct)}>
                          {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(2)}%
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-sm">
                    <div className="text-[var(--text-dim)]">
                      阈值 {item.alert_threshold != null ? `${(Number(item.alert_threshold) * 100).toFixed(0)}%` : '-'}
                    </div>
                    <Tag>{new Date(item.created_at).toLocaleString()}</Tag>
                  </div>
                  <div className="mt-3">
                    <Link href={`/depth?code=${item.stock_code}`} className="ve-tab-button text-xs px-2 py-1">
                      深度分析 →
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <EmptyState title="暂无预警记录" description="当自选股价格触发阈值时，预警记录会自动保存在这里。" action={<Link href="/watchlist" className="ve-button-primary">前往监控台</Link>} />
        )}
      </SurfaceCard>
    </AppPage>
  )
}
