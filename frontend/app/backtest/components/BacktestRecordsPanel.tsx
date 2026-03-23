import { Button, Card, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import Link from 'next/link'
import type { RunItem } from './types'

const { Text } = Typography

function fmtPct(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return `${(num * 100).toFixed(2)}%`
}

function getRecordColumns(onViewDetail: (runId: number) => void): ColumnsType<RunItem> {
  return [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 80, render: (v) => `#${v}` },
    { title: '名称', dataIndex: 'name', key: 'name', width: 140, ellipsis: true },
    { title: '策略', dataIndex: 'strategy_id', key: 'strategy_id', width: 160, ellipsis: true },
    {
      title: '区间',
      key: 'range',
      width: 220,
      render: (_, r) => `${r.start_date} ~ ${r.end_date}`,
    },
    {
      title: '总收益',
      dataIndex: ['summary', 'total_return'],
      key: 'total_return',
      width: 100,
      align: 'right',
      render: (v) => fmtPct(v),
    },
    {
      title: '最大回撤',
      dataIndex: ['summary', 'max_drawdown'],
      key: 'max_drawdown',
      width: 100,
      align: 'right',
      render: (v) => fmtPct(v),
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: (v) => <Tag>{v}</Tag> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180, render: (v) => new Date(v).toLocaleString() },
    {
      title: '操作',
      key: 'action',
      width: 110,
      render: (_, r) => (
        <Button type="link" onClick={() => onViewDetail(r.id)}>
          查看详情
        </Button>
      ),
    },
  ]
}

export function BacktestRecordsPanel({
  runs,
  runsLoading,
  onRefresh,
  onViewDetail,
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: {
  runs: RunItem[]
  runsLoading: boolean
  onRefresh: () => void
  onViewDetail: (runId: number) => void
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}) {
  return (
    <Card
      title="回测记录"
      extra={(
        <Button type="link" onClick={onRefresh}>刷新</Button>
      )}
    >
      <div className="hidden md:block">
        <Table<RunItem>
          rowKey="id"
          bordered
          size="small"
          loading={runsLoading}
          columns={getRecordColumns(onViewDetail)}
          dataSource={runs}
          scroll={{ x: 1100 }}
          locale={{ emptyText: '暂无回测记录' }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100],
            onChange: (nextPage, nextPageSize) => {
              if (nextPageSize !== pageSize) onPageSizeChange(nextPageSize)
              onPageChange(nextPage)
            },
            showTotal: (count) => `共 ${count} 条`,
          }}
        />
      </div>

      <div className="md:hidden space-y-2">
        {runs.map((r) => (
          <Card key={r.id} size="small">
            <div className="space-y-1 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium truncate">{r.name}</span>
                <Tag>{r.status}</Tag>
              </div>
              <div><Text code>{r.strategy_id}</Text></div>
              <div>{r.start_date} ~ {r.end_date}</div>
              <div>总收益：{fmtPct(r.summary?.total_return)}</div>
              <div>最大回撤：{fmtPct(r.summary?.max_drawdown)}</div>
              <div className="text-gray-500">{new Date(r.created_at).toLocaleString()}</div>
              <Link href="#" onClick={(e) => { e.preventDefault(); onViewDetail(r.id) }} className="text-indigo-600">查看详情</Link>
            </div>
          </Card>
        ))}
        {runs.length === 0 && <div className="text-sm text-gray-500">暂无回测记录</div>}
      </div>
    </Card>
  )
}
