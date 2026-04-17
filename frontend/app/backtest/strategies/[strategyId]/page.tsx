'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import axios from 'axios'
import { Breadcrumb, Segmented, Skeleton, Tag } from 'antd'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { getAuthHeader, isAuthenticated } from '../../../lib/auth'
import { formatDrawdownPct, formatPct, marketClassByDrawdown, marketClassByValue } from '../../../lib/marketColors'
import type { StrategyManagementDetail } from '../../components/types'
import { AppPage, InfoPill, MetricCard, SurfaceCard } from '../../../components/ui-shell'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

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
    <AppPage>
      <div className="space-y-4">
        <Breadcrumb
          items={[
            { title: <Link href="/backtest">回测中心</Link> },
            { title: <Link href="/backtest?tab=strategies">策略管理</Link> },
            { title: strategyId || '详情' },
          ]}
        />

        {loading ? (
          <div className="ve-panel"><Skeleton active paragraph={{ rows: 10 }} /></div>
        ) : !detail ? (
          <div className="ve-panel">未找到策略详情</div>
        ) : (
          <>
            <SurfaceCard
              title={detail.strategy_info.name}
              description="策略详情页统一展示基本信息、最近回测成绩和代码内容，便于从回测结果回溯到实现。"
              actions={(
                <div className="flex flex-wrap gap-2">
                  <InfoPill>{detail.strategy_info.strategy_id}</InfoPill>
                  <InfoPill>{detail.strategy_info.usable ? '可用' : '不可用'}</InfoPill>
                </div>
              )}
            >
              <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
                <MetricCard label="策略 ID" value={<code className="text-sm">{detail.strategy_info.strategy_id}</code>} meta={`最近修改 ${fmtTime(detail.strategy_info.last_modified_at || undefined)}`} tone="brand" icon="◎" />
                <MetricCard label="可用性" value={<Tag color={detail.strategy_info.usable ? 'green' : 'red'}>{detail.strategy_info.usable ? '可用' : '不可用'}</Tag>} meta="用于区分可直接参与回测的策略。" icon="◌" />
                <MetricCard label="代码视图" value={codeTab === 'core' ? '核心片段' : '源码全文'} meta="支持在两种粒度间切换。" icon="{ }" />
                <MetricCard label="最近修改" value={fmtTime(detail.strategy_info.last_modified_at || undefined)} meta="用于确认当前策略版本是否为最新。" tone="warning" icon="↻" />
              </div>
            </SurfaceCard>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
              {detail.latest_backtest ? (
                <>
                  <MetricCard label="年化" value={<span className={marketClassByValue(detail.latest_backtest.annual_return)}>{formatPct(detail.latest_backtest.annual_return)}</span>} meta="最近一次有效回测结果" tone="brand" icon="↗" />
                  <MetricCard label="总收益" value={<span className={marketClassByValue(detail.latest_backtest.total_return)}>{formatPct(detail.latest_backtest.total_return)}</span>} meta="累计收益" icon="∑" />
                  <MetricCard label="夏普" value={detail.latest_backtest.sharpe ?? '-'} meta="风险调整后收益" icon="≈" />
                  <MetricCard label="最大回撤" value={<span className={marketClassByDrawdown(detail.latest_backtest.max_drawdown)}>{formatDrawdownPct(detail.latest_backtest.max_drawdown)}</span>} meta="回撤按交易终端语义显示" tone="warning" icon="↘" />
                </>
              ) : (
                <div className="md:col-span-4 ve-panel">暂无回测结果</div>
              )}
            </div>

            <SurfaceCard
              title="策略代码"
              description="默认先看核心片段，必要时再切换到完整源码，减少长代码块带来的认知负担。"
              actions={(
                <Segmented
                  value={codeTab}
                  options={[
                    { label: '核心片段', value: 'core' },
                    { label: '源码全文', value: 'full' },
                  ]}
                  onChange={(v) => setCodeTab(v as 'core' | 'full')}
                />
              )}
            >
              <div className="overflow-hidden rounded-[22px] border border-[var(--border-subtle)]">
                <SyntaxHighlighter
                  language="python"
                  style={oneDark}
                  customStyle={{ margin: 0, fontSize: '12px', minWidth: '100%', maxHeight: '70vh' }}
                  wrapLongLines={false}
                  showLineNumbers
                >
                  {codeText || ''}
                </SyntaxHighlighter>
              </div>
            </SurfaceCard>
          </>
        )}
      </div>
    </AppPage>
  )
}
