import { Alert, Button, Space, Tag } from 'antd'
import type { BacktestObservability } from './types'
import { BOARD_LABELS } from './types'
import { StatusTag } from './statusLabels'
import { FunnelCard } from './FunnelCard'

const BOARD_KEYS = ['main', 'gem', 'star', 'bse'] as const

function BoardTags({
  byBoard,
  byBoardExcludeSt,
}: {
  byBoard?: Record<string, number>
  byBoardExcludeSt?: Record<string, number>
}) {
  return (
    <Space wrap size={[4, 4]}>
      {BOARD_KEYS.map((k) => {
        const total = byBoard?.[k] ?? 0
        const exSt = byBoardExcludeSt?.[k] ?? 0
        return (
          <Tag key={k} color="default">
            {BOARD_LABELS[k]}：{total.toLocaleString()}
            {exSt > 0 ? <span className="text-[var(--text-dim)]">（非ST {exSt.toLocaleString()}）</span> : null}
          </Tag>
        )
      })}
    </Space>
  )
}

/**
 * 全市场扫描运行观测面板：股票池覆盖、任务生命周期计数，以及最近扫描记录的
 * 漏斗诊断。数据来自 GET /api/backtest/observability。
 */
export function ObservabilityPanel({
  observability,
  loading,
  error,
  onRefresh,
}: {
  observability: BacktestObservability | null
  loading: boolean
  error: string | null
  onRefresh: () => void
}) {
  const universe = observability?.universe
  const counters = observability?.counters
  const runs = observability?.recent_scan_runs || []

  const jobCount = counters?.jobs || {}
  const jobCountItems: Array<{ key: string; label: string; tone: string }> = [
    { key: 'pending', label: '排队中', tone: 'text-[var(--text-muted)]' },
    { key: 'running', label: '运行中', tone: 'text-[var(--brand-strong)]' },
    { key: 'success', label: '成功', tone: 'text-[var(--up)]' },
    { key: 'failed', label: '失败', tone: 'text-[var(--down)]' },
    { key: 'cancelled', label: '已取消', tone: 'text-[var(--text-muted)]' },
  ]

  return (
    <section className="ve-panel">
      <div className="mb-5 flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">运行观测</h2>
          {loading ? <span className="inline-block h-2 w-2 rounded-full bg-[var(--brand)] animate-pulse" title="加载中" /> : null}
        </div>
        <Button type="default" onClick={onRefresh} loading={loading}>
          刷新
        </Button>
      </div>

      {error ? (
        <Alert
          type="warning"
          showIcon
          message="观测数据拉取失败"
          description={error}
          action={
            <Button size="small" onClick={onRefresh}>
              重试
            </Button>
          }
        />
      ) : (
        <div className="space-y-4">
          {/* 股票池 + 任务计数 */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2.5">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">股票池总数</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-[var(--text-strong)]">
                {(universe?.total_active ?? 0).toLocaleString()}
              </div>
            </div>
            <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2.5">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">非ST</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-[var(--text-strong)]">
                {(universe?.non_st_active ?? 0).toLocaleString()}
              </div>
            </div>
            <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2.5">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">ST</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-[var(--text-strong)]">
                {(universe?.st_active ?? 0).toLocaleString()}
              </div>
            </div>
            <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2.5">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">回测记录</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-[var(--text-strong)]">
                {(counters?.runs?.total ?? 0).toLocaleString()}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2 text-sm">
            {jobCountItems.map((item) => (
              <Tag key={item.key} color="default">
                {item.label} <span className={`font-semibold tabular-nums ${item.tone}`}>{jobCount[item.key] ?? 0}</span>
              </Tag>
            ))}
          </div>

          <BoardTags byBoard={universe?.by_board} byBoardExcludeSt={universe?.by_board_exclude_st} />

          {/* 最近扫描记录的漏斗诊断 */}
          {runs.length > 0 ? (
            <div className="space-y-3">
              <div className="text-sm font-semibold text-[var(--text-strong)]">最近扫描诊断</div>
              {runs.map((run) => (
                <div key={run.run_id} className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] p-4">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <span className="font-medium text-[var(--text-strong)]">{run.name || `扫描 #${run.run_id}`}</span>
                      <span className="ml-2 text-xs text-[var(--text-dim)]">{run.start_date} ~ {run.end_date}</span>
                    </div>
                    <StatusTag status={run.status || 'completed'} />
                  </div>
                  <FunnelCard diagnostics={run.diagnostics} warnings={run.warnings} title="漏斗分析" />
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--border)] px-4 py-6 text-center text-sm text-[var(--text-dim)]">
              暂无带诊断的全市场扫描记录，运行一次 strategy_select 回测后这里会显示漏斗。
            </div>
          )}
        </div>
      )}
    </section>
  )
}
