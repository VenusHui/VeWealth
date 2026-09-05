import { Alert, Button, Progress, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import Link from 'next/link'
import type { BacktestObservability, JobItem, RunItem } from './types'
import { formatDrawdownPct, formatPct, marketClassByDrawdown, marketClassByValue } from '../../lib/marketColors'
import { StatusTag } from './statusLabels'
import { isActiveStage, stageLabel } from '../status'
import { clampPct } from '../calc'
import { ObservabilityPanel } from './ObservabilityPanel'

const ACTIVE_STATUSES = ['pending', 'running']

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

function formatEta(etaSeconds?: number | null): string {
  if (etaSeconds == null || !Number.isFinite(etaSeconds)) return '—'
  if (etaSeconds < 60) return `约 ${Math.round(etaSeconds)} 秒`
  if (etaSeconds < 3600) return `约 ${Math.round(etaSeconds / 60)} 分钟`
  return `约 ${(etaSeconds / 3600).toFixed(1)} 小时`
}

function JobTaskCard({
  job,
  onCancelJob,
  onRetryJob,
}: {
  job: JobItem
  onCancelJob: (jobId: string) => void
  onRetryJob: (jobId: string) => void
}) {
  const pct = clampPct(job.progress_pct)
  const active = ACTIVE_STATUSES.includes(job.status) || isActiveStage(job.stage)
  const retryable = job.status === 'failed' || job.status === 'cancelled'
  const processed = job.processed_symbols ?? 0
  const total = job.total_symbols ?? 0

  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-[var(--text-strong)]">
            {job.name || job.job_id.slice(0, 8)}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-sm text-[var(--text-dim)]">
            <StatusTag status={job.status} />
            <Tag color="default" className="text-xs">
              {stageLabel(job.stage)}
            </Tag>
            {total > 0 ? (
              <span className="tabular-nums">{processed.toLocaleString()} / {total.toLocaleString()} 只</span>
            ) : null}
            <span className="tabular-nums">ETA {formatEta(job.eta_seconds)}</span>
            {job.created_at ? <span>{new Date(job.created_at).toLocaleString()}</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {active ? (
            <Button size="small" onClick={() => onCancelJob(job.job_id)}>
              取消
            </Button>
          ) : null}
          {retryable ? (
            <Button size="small" type="primary" onClick={() => onRetryJob(job.job_id)}>
              重试
            </Button>
          ) : null}
        </div>
      </div>

      {(active || pct > 0) && (
        <div className="mt-3">
          <Progress
            percent={pct}
            size="small"
            showInfo={false}
            strokeColor="var(--brand)"
            trailColor="var(--border-subtle)"
          />
        </div>
      )}

      {job.error ? (
        <div className="mt-2 rounded-[var(--radius-control)] border border-[rgba(190,18,60,0.16)] bg-[rgba(254,242,242,0.7)] px-3 py-2 text-sm text-[var(--down)]">
          {job.error}
        </div>
      ) : null}
    </div>
  )
}

export function BacktestRecordsPanel({
  runs,
  runsLoading,
  jobs,
  onCancelJob,
  onRetryJob,
  onRefresh,
  onViewDetail,
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pollingActive = false,
  jobsError,
  observability,
  observabilityLoading,
  observabilityError,
  onRefreshObservability,
}: {
  runs: RunItem[]
  runsLoading: boolean
  jobs: JobItem[]
  onCancelJob: (jobId: string) => void
  onRetryJob: (jobId: string) => void
  onRefresh: () => void
  onViewDetail: (runId: number) => void
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  pollingActive?: boolean
  jobsError?: string | null
  observability: BacktestObservability | null
  observabilityLoading: boolean
  observabilityError: string | null
  onRefreshObservability: () => void
}) {
  // 任务卡只展示「仍在跑 / 失败 / 取消」的可操作任务；已完成任务走下方回测记录表，
  // 避免与记录表重复堆叠。完成态任务（success/completed）不占任务卡。
  const actionableJobs = jobs.filter((j) => ACTIVE_STATUSES.includes(j.status) || j.status === 'failed' || j.status === 'cancelled')
  const isEmpty = runs.length === 0

  return (
    <div className="space-y-4">
      {/* 运行观测：股票池覆盖 + 最近扫描诊断 */}
      <ObservabilityPanel
        observability={observability}
        loading={observabilityLoading}
        error={observabilityError}
        onRefresh={onRefreshObservability}
      />

      <section className="ve-panel">
        <div className="mb-5 flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-4 md:flex-row md:items-start md:justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">回测任务</h2>
            {pollingActive ? (
              <span className="inline-block h-2 w-2 rounded-full bg-[var(--brand)] animate-pulse" title="自动刷新中" />
            ) : null}
          </div>
          <Button type="default" onClick={onRefresh}>刷新</Button>
        </div>

        {jobsError ? (
          <Alert
            type="warning"
            showIcon
            message="任务数据拉取失败"
            description={jobsError}
            action={<Button size="small" onClick={onRefresh}>重试</Button>}
          />
        ) : null}

        {/* 任务卡：展示阶段 / 进度 / 处理数 / ETA / 错误 / 取消 / 重试 */}
        <div className="mb-4 space-y-3">
          {actionableJobs.length > 0 ? (
            actionableJobs.map((j) => (
              <JobTaskCard key={j.job_id} job={j} onCancelJob={onCancelJob} onRetryJob={onRetryJob} />
            ))
          ) : (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--border)] px-4 py-8 text-sm text-[var(--text-dim)]">
              暂无运行中的回测任务。完成的回测记录见下方列表。
            </div>
          )}
        </div>
      </section>

      <section className="ve-panel">
        <div className="mb-5 flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-4 md:flex-row md:items-start md:justify-between">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">回测记录</h2>
        </div>

        {/* Desktop: completed runs table */}
        <div className="hidden md:block">
          <Table<RunItem>
            rowKey="id"
            size="small"
            loading={runsLoading}
            columns={getRecordColumns(onViewDetail)}
            dataSource={runs}
            scroll={{ x: 1100, y: 480 }}
            locale={{ emptyText: isEmpty ? '暂无回测记录，提交新的回测任务后结果将显示在这里' : '暂无已完成记录' }}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
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
            <div key={r.id} className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] p-4">
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
          {isEmpty ? <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--border)] px-4 py-8 text-sm text-[var(--text-dim)]">暂无回测记录，提交新的回测任务后结果将显示在这里</div> : null}
        </div>
      </section>
    </div>
  )
}
