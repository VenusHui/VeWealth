'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import axios from 'axios'
import { Breadcrumb, Card, Segmented, Skeleton, Tag, Typography } from 'antd'
import { getAuthHeader, isAuthenticated } from '../../../lib/auth'
import type { StrategyManagementDetail } from '../../components/types'

const { Text } = Typography
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

function fmtPct(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return `${(num * 100).toFixed(2)}%`
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

export default function StrategyDetailPage() {
  const params = useParams<{ strategyId: string }>()
  const strategyId = decodeURIComponent(params?.strategyId || '')
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<StrategyManagementDetail | null>(null)
  const [codeTab, setCodeTab] = useState<'core' | 'full'>('core')

  useEffect(() => {
    if (!strategyId || !isAuthenticated()) return
    const run = async () => {
      setLoading(true)
      try {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/strategy-management/${strategyId}`, {
          headers: getAuthHeader(),
        })
        setDetail((resp.data?.data || null) as StrategyManagementDetail | null)
      } catch {
        setDetail(null)
      } finally {
        setLoading(false)
      }
    }
    run()
  }, [strategyId])

  const codeText = useMemo(() => {
    if (!detail) return ''
    return codeTab === 'core' ? detail.code?.core_snippet || '暂无核心片段' : detail.code?.full_source || '暂无源码'
  }, [codeTab, detail])

  if (!isAuthenticated()) {
    return <div className="app-page-shell"><div className="app-page-container">请先登录后使用。</div></div>
  }

  return (
    <div className="app-page-shell">
      <div className="app-page-container app-section-stack">
        <Breadcrumb
          items={[
            { title: <Link href="/backtest">回测中心</Link> },
            { title: <Link href="/backtest?tab=strategies">策略管理</Link> },
            { title: strategyId || '详情' },
          ]}
        />

        {loading ? (
          <Card><Skeleton active paragraph={{ rows: 8 }} /></Card>
        ) : !detail ? (
          <Card>未找到策略详情</Card>
        ) : (
          <>
            <Card title="策略信息">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                <div><Text strong>策略名：</Text>{detail.strategy_info.name}</div>
                <div><Text strong>策略ID：</Text><Text code>{detail.strategy_info.strategy_id}</Text></div>
                <div><Text strong>可用性：</Text><Tag color={detail.strategy_info.usable ? 'green' : 'red'}>{detail.strategy_info.usable ? '可用' : '不可用'}</Tag></div>
                <div><Text strong>最近修改：</Text>{fmtTime(detail.strategy_info.last_modified_at || undefined)}</div>
              </div>
            </Card>

            <Card title="最近回测成绩">
              {detail.latest_backtest ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                  <div><Text strong>年化：</Text>{fmtPct(detail.latest_backtest.annual_return)}</div>
                  <div><Text strong>总收益：</Text>{fmtPct(detail.latest_backtest.total_return)}</div>
                  <div><Text strong>夏普：</Text>{detail.latest_backtest.sharpe ?? '-'}</div>
                  <div><Text strong>最大回撤：</Text>{fmtPct(detail.latest_backtest.max_drawdown)}</div>
                </div>
              ) : (
                <div className="text-sm text-gray-500">暂无回测</div>
              )}
            </Card>

            <Card title="策略代码">
              <div className="mb-3">
                <Segmented
                  value={codeTab}
                  options={[
                    { label: '核心片段', value: 'core' },
                    { label: '源码全文', value: 'full' },
                  ]}
                  onChange={(v) => setCodeTab(v as 'core' | 'full')}
                />
              </div>
              <pre className="bg-gray-900 text-gray-100 text-xs p-3 rounded-lg overflow-auto max-h-[70vh]">
                {codeText}
              </pre>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
