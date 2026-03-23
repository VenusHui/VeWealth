import { Button, Card, Input, Segmented, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import type {
  StrategyManagementDetail,
  StrategyManagementListItem,
} from './types'

const { Text } = Typography

function fmtPct(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return `${(num * 100).toFixed(2)}%`
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function getColumns(
  onOpenDetail: (strategyId: string) => void,
): ColumnsType<StrategyManagementListItem> {
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
      width: 200,
      ellipsis: true,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '最近修改',
      dataIndex: 'last_modified_at',
      key: 'last_modified_at',
      width: 180,
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
        return <span className={num >= 0 ? 'text-emerald-600' : 'text-rose-600'}>{fmtPct(v)}</span>
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
        <Button type="link" onClick={() => onOpenDetail(row.strategy_id)}>
          查看详情
        </Button>
      ),
    },
  ]
}

export function StrategyManagementPanel({
  loading,
  detailLoading,
  items,
  detail,
  onRefresh,
  onOpenDetail,
}: {
  loading: boolean
  detailLoading: boolean
  items: StrategyManagementListItem[]
  detail: StrategyManagementDetail | null
  onRefresh: () => void
  onOpenDetail: (strategyId: string) => void
}) {
  const [query, setQuery] = useState('')
  const [usableFilter, setUsableFilter] = useState<'all' | 'usable' | 'unusable'>('all')
  const [codeTab, setCodeTab] = useState<'core' | 'full'>('core')

  const filteredItems = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return items.filter((item) => {
      if (usableFilter === 'usable' && !item.usable) return false
      if (usableFilter === 'unusable' && item.usable) return false
      if (!keyword) return true
      return (
        item.name.toLowerCase().includes(keyword) ||
        item.strategy_id.toLowerCase().includes(keyword)
      )
    })
  }, [items, query, usableFilter])

  const codeText =
    codeTab === 'core'
      ? detail?.code?.core_snippet || '暂无核心片段'
      : detail?.code?.full_source || '暂无源码'

  return (
    <div className="space-y-4">
      <Card
        title="策略管理"
        extra={<Button type="link" onClick={onRefresh}>刷新</Button>}
      >
        <Space className="mb-3" wrap>
          <Input.Search
            allowClear
            placeholder="按策略名/strategy_id 搜索"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: 280 }}
          />
          <Segmented
            value={usableFilter}
            options={[
              { label: '全部', value: 'all' },
              { label: '仅可用', value: 'usable' },
              { label: '仅不可用', value: 'unusable' },
            ]}
            onChange={(v) => setUsableFilter(v as 'all' | 'usable' | 'unusable')}
          />
        </Space>

        <Table<StrategyManagementListItem>
          rowKey="strategy_id"
          bordered
          size="small"
          loading={loading}
          columns={getColumns(onOpenDetail)}
          dataSource={filteredItems}
          scroll={{ x: 980 }}
          pagination={false}
          locale={{ emptyText: '暂无策略' }}
        />
      </Card>

      <Card title="策略详情" loading={detailLoading}>
        {!detail ? (
          <div className="text-gray-500 text-sm">请选择一条策略查看详情</div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div>
                <Text strong>策略：</Text>{detail.strategy_info.name} <Text code>{detail.strategy_info.strategy_id}</Text>
              </div>
              <div>
                <Text strong>最近修改：</Text>{fmtTime(detail.strategy_info.last_modified_at || undefined)}
              </div>
              <div>
                <Text strong>最近回测年化：</Text>{fmtPct(detail.latest_backtest?.annual_return)}
              </div>
              <div>
                <Text strong>代码行数：</Text>{detail.code?.line_count ?? 0}
              </div>
            </div>

            <Segmented
              value={codeTab}
              options={[
                { label: '核心片段', value: 'core' },
                { label: '源码全文', value: 'full' },
              ]}
              onChange={(v) => setCodeTab(v as 'core' | 'full')}
            />

            <pre className="bg-gray-900 text-gray-100 text-xs p-3 rounded-lg overflow-auto max-h-[520px]">
              {codeText}
            </pre>
          </div>
        )}
      </Card>
    </div>
  )
}
