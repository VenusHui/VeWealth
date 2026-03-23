import Link from 'next/link'
import { Button, Card, Input, Pagination, Segmented, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo } from 'react'
import type { StrategyManagementListItem } from './types'
import { formatPct, marketClassByValue } from '../../lib/marketColors'

const { Text } = Typography

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
      render: (v: string) => <Text code>{v}</Text>,
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
        <Link href={`/backtest/strategies/${row.strategy_id}`} className="text-indigo-600 hover:text-indigo-800">
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
          <Card key={item.strategy_id} size="small" className="mb-2">
            <div className="space-y-1 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium truncate">{item.name}</span>
                <Tag color={item.usable ? 'green' : 'red'}>{item.usable ? '可用' : '不可用'}</Tag>
              </div>
              <div><Text code>{item.strategy_id}</Text></div>
              <div>最近修改：{fmtTime(item.last_modified_at || undefined)}</div>
              <div>
                年化：
                {Number.isFinite(annual) ? (
                  <span className={marketClassByValue(annual)}>{formatPct(annual)}</span>
                ) : (
                  '-'
                )}
              </div>
              <div>
                <Link href={`/backtest/strategies/${item.strategy_id}`} className="text-indigo-600 hover:text-indigo-800">
                  查看详情
                </Link>
              </div>
            </div>
          </Card>
        )
      }),
    [items],
  )

  return (
    <Card title="策略管理" extra={<Button type="link" onClick={onRefresh}>刷新</Button>}>
      <Space className="mb-3 w-full" wrap>
        <Input.Search
          allowClear
          placeholder="按策略名/strategy_id 搜索"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          style={{ width: 280, maxWidth: '100%' }}
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
      </Space>

      <div className="hidden md:block">
        <Table<StrategyManagementListItem>
          rowKey="strategy_id"
          bordered
          size="small"
          loading={loading}
          columns={getColumns()}
          dataSource={items}
          scroll={{ x: 980 }}
          pagination={false}
          locale={{ emptyText: '暂无策略' }}
        />
      </div>

      <div className="md:hidden">{mobileCards.length ? mobileCards : <div className="text-sm text-gray-500">暂无策略</div>}</div>

      <div className="mt-3 flex justify-end">
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
    </Card>
  )
}
