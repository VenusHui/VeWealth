import Link from 'next/link'
import { Button, Input, Pagination, Segmented, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo } from 'react'
import type { StrategyManagementListItem } from './types'
import { formatPct, marketClassByValue } from '../../lib/marketColors'

function fmtTime(value: string | null | undefined): string {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function getColumns(): ColumnsType<StrategyManagementListItem> {
  return [
    {
      title: '策略名',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      ellipsis: true,
    },
    {
      title: 'strategy_id',
      dataIndex: 'strategy_id',
      key: 'strategy_id',
      width: 220,
      ellipsis: true,
      render: (v: string) => <code className="rounded bg-slate-100 px-1.5 py-1 text-xs">{v}</code>,
    },
    {
      title: '最近修改',
      dataIndex: 'last_modified_at',
      key: 'last_modified_at',
      width: 190,
      render: (v: string) => fmtTime(v),
    },
    {
      title: '最近回测年化',
      dataIndex: ['latest_backtest', 'annual_return'],
      key: 'annual_return',
      width: 140,
      align: 'right',
      render: (v: unknown) => {
        const num = Number(v)
        if (!Number.isFinite(num)) return '-'
        return <span className={marketClassByValue(num)}>{formatPct(v)}</span>
      },
    },
    {
      title: '可用',
      dataIndex: 'usable',
      key: 'usable',
      width: 90,
      render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '可用' : '不可用'}</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, row) => (
        <Link href={`/backtest/strategies/${row.strategy_id}`} className="text-[var(--brand-strong)] hover:text-[var(--brand)]">
          查看详情
        </Link>
      ),
    },
  ]
}

export function StrategyManagementPanel({
  loading,
  items,
  query,
  usableFilter,
  total,
  page,
  pageSize,
  onRefresh,
  onQueryChange,
  onUsableFilterChange,
  onPageChange,
  onPageSizeChange,
}: {
  loading: boolean
  items: StrategyManagementListItem[]
  query: string
  usableFilter: 'all' | 'true' | 'false'
  total: number
  page: number
  pageSize: number
  onRefresh: () => void
  onQueryChange: (value: string) => void
  onUsableFilterChange: (value: 'all' | 'true' | 'false') => void
  onPageChange: (value: number) => void
  onPageSizeChange: (value: number) => void
}) {
  const mobileCards = useMemo(
    () =>
      items.map((item) => {
        const annual = Number(item.latest_backtest?.annual_return)
        return (
          <div key={item.strategy_id} className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.75)] p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-semibold text-[var(--text-strong)]">{item.name}</span>
              <Tag color={item.usable ? 'green' : 'red'}>{item.usable ? '可用' : '不可用'}</Tag>
            </div>
            <div className="mt-2 text-sm text-[var(--text-dim)]"><code>{item.strategy_id}</code></div>
            <div className="mt-3 space-y-1 text-sm text-[var(--text-muted)]">
              <div>最近修改：{fmtTime(item.last_modified_at || undefined)}</div>
              <div>年化：{Number.isFinite(annual) ? <span className={marketClassByValue(annual)}>{formatPct(annual)}</span> : '-'}</div>
            </div>
            <div className="mt-3">
              <Link href={`/backtest/strategies/${item.strategy_id}`} className="text-sm font-medium text-[var(--brand-strong)] hover:text-[var(--brand)]">
                查看详情 →
              </Link>
            </div>
          </div>
        )
      }),
    [items],
  )

  return (
    <section className="ve-panel">
      <div className="mb-5 flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">策略管理</h2>
          <p className="text-sm leading-6 text-[var(--text-muted)]">按可用性和关键字筛选策略，再进入详情页查看回测成绩和源码。</p>
        </div>
        <Button type="default" onClick={onRefresh}>刷新</Button>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <Input.Search
          allowClear
          placeholder="按策略名 / strategy_id 搜索"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          style={{ width: 300, maxWidth: '100%' }}
        />
        <Segmented
          value={usableFilter}
          options={[
            { label: '全部', value: 'all' },
            { label: '仅可用', value: 'true' },
            { label: '仅不可用', value: 'false' },
          ]}
          onChange={(v) => onUsableFilterChange(v as 'all' | 'true' | 'false')}
        />
      </div>

      <div className="hidden md:block">
        <Table<StrategyManagementListItem>
          rowKey="strategy_id"
          size="small"
          loading={loading}
          columns={getColumns()}
          dataSource={items}
          scroll={{ x: 980 }}
          pagination={false}
          locale={{ emptyText: '暂无策略' }}
        />
      </div>

      <div className="space-y-3 md:hidden">{mobileCards.length ? mobileCards : <div className="rounded-[20px] border border-dashed border-[var(--border)] px-4 py-8 text-sm text-[var(--text-dim)]">暂无策略</div>}</div>

      <div className="mt-4 flex justify-end">
        <Pagination
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          pageSizeOptions={[10, 20, 50]}
          onChange={(nextPage, nextSize) => {
            if (nextSize !== pageSize) onPageSizeChange(nextSize)
            onPageChange(nextPage)
          }}
          showTotal={(count) => `共 ${count} 条`}
        />
      </div>
    </section>
  )
}
