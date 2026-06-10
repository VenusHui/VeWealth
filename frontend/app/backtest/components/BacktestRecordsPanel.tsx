import { Button, Progress, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import Link from 'next/link'
import type { JobItem, RunItem } from './types'
import { formatDrawdownPct, formatPct, marketClassByDrawdown, marketClassByValue } from '../../lib/marketColors'
import { StatusTag } from './statusLabels'

function getRecordColumns(onViewDetail: (runId: number) => void): ColumnsType<RunItem> {
  return [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 80, render: (v) => `#${v}` },
    { title: '名称', dataIndex: 'name', key: 'name', width: 160, ellipsis: true },
    { title: '策略', dataIndex: 'strategy_id', key: 'strategy_id', width: 170, ellipsis: true },
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
      render: (v) => <span className={marketClassByValue(v)}>{formatPct(v)}</span>,
    },
    {
      title: '最大回撤',
      dataIndex: ['summary', 'max_drawdown'],
      key: 'max_drawdown',
      width: 100,
      align: 'right',
      render: (v) => <span className={marketClassByDrawdown(v)}>{formatDrawdownPct(v)}</span>,
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: (v) => <StatusTag status={v} /> },
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

function ActiveJobCard({ job }: { job: JobItem }) {
  const pct = Math.min(Math.max(Number(job.progress_pct || 0), 0), 100)
  return (
    <div className="rounded-[20px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.72)] px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-[var(--text-strong)]">
            {job.name || job.job_id.slice(0, 8)}
          </div>
          {job.created_at ? (
            <div className="text-sm text-[var(--text-dim)]">
              {new Date(job.created_at).toLocaleString()}
            </div>
          ) : null}
        </div>
        <StatusTag status={job.status} />
      </div>
      <div className="mt-3">
        <Progress
          percent={pct}
          size="small"
          showInfo={false}
          strokeColor={{ '0%': '#0f766e', '100%': '#0d9488' }}
          trailColor="rgba(15,23,42,0.06)"
        />
      </div>
    </div>
  )
}

export function BacktestRecordsPanel({
  runs,
  runsLoading,
  activeJobs,
  onRefresh,
  onViewDetail,
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pollingActive = false,
}: {
  runs: RunItem[]
  runsLoading: boolean
  activeJobs: JobItem[]
  onRefresh: () => void
  onViewDetail: (runId: number) => void
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  pollingActive?: boolean
}) {
  const isEmpty = activeJobs.length === 0 && runs.length === 0

  return (
    <section className="ve-panel">
      <div className="mb-5 flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">回测记录</h2>
          {pollingActive ? (
            <span className="inline-block h-2 w-2 rounded-full bg-[var(--brand)] animate-pulse" title="自动刷新中" />
          ) : null}
        </div>
        <Button type="default" onClick={onRefresh}>刷新</Button>
      </div>

      {/* Active jobs — shown as cards in both desktop and mobile */}
      {activeJobs.length > 0 && (
        <div className="mb-4 space-y-3">
          {activeJobs.map((j) => (
            <ActiveJobCard key={j.job_id} job={j} />
          ))}
        </div>
      )}

      {/* Desktop: completed runs table */}
      <div className="hidden md:block">
        <Table<RunItem>
          rowKey="id"
          size="small"
          loading={runsLoading}
          columns={getRecordColumns(onViewDetail)}
          dataSource={runs}
          scroll={{ x: 1100 }}
          locale={{ emptyText: isEmpty ? '暂无回测记录，提交新的回测任务后结果将显示在这里' : '暂无已完成记录' }}
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

      {/* Mobile: completed runs cards */}
      <div className="space-y-3 md:hidden">
        {runs.map((r) => (
          <div key={r.id} className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.75)] p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-semibold text-[var(--text-strong)]">{r.name}</div>
                <div className="truncate text-sm text-[var(--text-dim)]">{r.strategy_id}</div>
              </div>
              <StatusTag status={r.status} />
            </div>
            <div className="mt-3 space-y-1 text-sm text-[var(--text-muted)]">
              <div>{r.start_date} ~ {r.end_date}</div>
              <div>总收益：<span className={marketClassByValue(r.summary?.total_return)}>{formatPct(r.summary?.total_return)}</span></div>
              <div>最大回撤：<span className={marketClassByDrawdown(r.summary?.max_drawdown)}>{formatDrawdownPct(r.summary?.max_drawdown)}</span></div>
              <div>{new Date(r.created_at).toLocaleString()}</div>
            </div>
            <div className="mt-3">
              <Link href="#" onClick={(e) => { e.preventDefault(); onViewDetail(r.id) }} className="text-sm font-medium text-[var(--brand-strong)]">
                查看详情 →
              </Link>
            </div>
          </div>
        ))}
        {isEmpty ? <div className="rounded-[20px] border border-dashed border-[var(--border)] px-4 py-8 text-sm text-[var(--text-dim)]">暂无回测记录，提交新的回测任务后结果将显示在这里</div> : null}
      </div>
    </section>
  )
}
