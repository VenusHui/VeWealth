import { LoadingHint } from './LoadingHint'
import type { JobItem, Strategy } from './types'

const BOARD_LABELS: Record<'main' | 'gem' | 'star' | 'bse', string> = {
  main: '主板',
  gem: '创业板',
  star: '科创板',
  bse: '北交所',
}

export function BacktestCreatePanel({
  name,
  strategyId,
  strategies,
  mode,
  universeType,
  symbols,
  poolSymbols,
  initialCash,
  startDate,
  endDate,
  selectedStrategy,
  strategyParams,
  loading,
  error,
  job,
  jobs,
  jobsLoading,
  onNameChange,
  onStrategyChange,
  onModeChange,
  onUniverseTypeChange,
  onSymbolsChange,
  onPoolSymbolsChange,
  onInitialCashChange,
  onStartDateChange,
  onEndDateChange,
  onStrategyParamChange,
  boardFilters,
  excludeSt,
  onBoardFilterChange,
  onExcludeStChange,
  onSubmit,
}: {
  name: string
  strategyId: string
  strategies: Strategy[]
  mode: 'manual_symbols' | 'strategy_select'
  universeType: 'all' | 'custom'
  symbols: string
  poolSymbols: string
  initialCash: string
  startDate: string
  endDate: string
  selectedStrategy?: Strategy
  strategyParams: Record<string, string>
  loading: boolean
  error: string
  job: JobItem | null
  jobs: JobItem[]
  jobsLoading: boolean
  onNameChange: (v: string) => void
  onStrategyChange: (v: string) => void
  onModeChange: (v: 'manual_symbols' | 'strategy_select') => void
  onUniverseTypeChange: (v: 'all' | 'custom') => void
  onSymbolsChange: (v: string) => void
  onPoolSymbolsChange: (v: string) => void
  onInitialCashChange: (v: string) => void
  onStartDateChange: (v: string) => void
  onEndDateChange: (v: string) => void
  boardFilters: Array<'main' | 'gem' | 'star' | 'bse'>
  excludeSt: boolean
  onBoardFilterChange: (board: 'main' | 'gem' | 'star' | 'bse', checked: boolean) => void
  onExcludeStChange: (v: boolean) => void
  onStrategyParamChange: (k: string, v: string) => void
  onSubmit: () => void
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.35fr_0.65fr]">
      <section className="ve-panel space-y-5">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">新建回测任务</h2>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="ve-field-label">任务名称</label>
            <input className="ve-input" value={name} onChange={(e) => onNameChange(e.target.value)} placeholder="例如：2025 主板趋势轮动" />
          </div>
          <div>
            <label className="ve-field-label">策略</label>
            <select className="ve-select" value={strategyId} onChange={(e) => onStrategyChange(e.target.value)}>
              {strategies.map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="ve-field-label">回测模式</label>
            <select className="ve-select" value={mode} onChange={(e) => onModeChange(e.target.value as 'manual_symbols' | 'strategy_select')}>
              <option value="manual_symbols">手工股票池</option>
              <option value="strategy_select">策略自动选股</option>
            </select>
          </div>
          {mode === 'manual_symbols' ? (
            <div>
              <label className="ve-field-label">股票代码（逗号分隔）</label>
              <input className="ve-input" value={symbols} onChange={(e) => onSymbolsChange(e.target.value)} placeholder="000001, 600519, 300750" />
            </div>
          ) : (
            <div>
              <label className="ve-field-label">选股范围</label>
              <select className="ve-select" value={universeType} onChange={(e) => onUniverseTypeChange(e.target.value as 'all' | 'custom')}>
                <option value="all">全市场</option>
                <option value="custom">自定义股票池</option>
              </select>
            </div>
          )}
          {mode === 'strategy_select' && universeType === 'custom' ? (
            <div>
              <label className="ve-field-label">自定义股票池</label>
              <input className="ve-input" value={poolSymbols} onChange={(e) => onPoolSymbolsChange(e.target.value)} placeholder="用逗号分隔自定义股票池" />
            </div>
          ) : null}
          <div>
            <label className="ve-field-label">初始资金</label>
            <input className="ve-input" value={initialCash} onChange={(e) => onInitialCashChange(e.target.value)} placeholder="100000" inputMode="decimal" />
          </div>
          <div>
            <label className="ve-field-label">开始日期</label>
            <input type="date" className="ve-date-input" value={startDate} onChange={(e) => onStartDateChange(e.target.value)} />
          </div>
          <div>
            <label className="ve-field-label">结束日期</label>
            <input type="date" className="ve-date-input" value={endDate} onChange={(e) => onEndDateChange(e.target.value)} />
          </div>
        </div>

        {selectedStrategy ? (
          <div className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.68)] p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-[var(--text-strong)]">策略参数</div>
                <div className="text-sm text-[var(--text-dim)]">当前策略：{selectedStrategy.name}</div>
              </div>
              <div className="ve-info-pill">{selectedStrategy.strategy_id}</div>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {selectedStrategy.param_schema.map((p) => (
                <div key={p.key}>
                  <label className="ve-field-label">{p.label}</label>
                  <input className="ve-input" value={strategyParams[p.key] ?? ''} onChange={(e) => onStrategyParamChange(p.key, e.target.value)} placeholder={String(p.default ?? '')} />
                </div>
              ))}
            </div>

            {mode === 'strategy_select' ? (
              <div className="mt-5 rounded-[20px] border border-[var(--border-subtle)] bg-[rgba(248,250,252,0.84)] p-4">
                <div className="text-sm font-semibold text-[var(--text-strong)]">股票范围过滤</div>
                <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                  {(['main', 'gem', 'star', 'bse'] as const).map((key) => (
                    <label key={key} className="flex items-center gap-2 rounded-2xl border border-[var(--border-subtle)] bg-white px-3 py-3 text-sm text-[var(--text-muted)]">
                      <input type="checkbox" checked={boardFilters.includes(key)} onChange={(e) => onBoardFilterChange(key, e.target.checked)} />
                      <span>{BOARD_LABELS[key]}</span>
                    </label>
                  ))}
                </div>
                <label className="mt-4 flex items-center gap-2 text-sm text-[var(--text-muted)]">
                  <input type="checkbox" checked={excludeSt} onChange={(e) => onExcludeStChange(e.target.checked)} />
                  <span>排除 ST / *ST</span>
                </label>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={onSubmit} disabled={loading} className="ve-button-primary">
            {loading ? '提交中…' : '提交回测任务'}
          </button>
          <span className="text-sm text-[var(--text-dim)]">提交后进入任务队列，完成后出现在记录列表。</span>
        </div>

        {error ? <div className="rounded-2xl border border-[rgba(220,38,38,0.16)] bg-[rgba(254,242,242,0.86)] px-4 py-3 text-sm text-red-700">{error}</div> : null}
      </section>

      <section className="ve-panel space-y-4">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">任务状态</h2>
        </div>

        {job ? (
          <div className="ve-metric-card ve-metric-card--brand">
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-dim)]">当前任务</div>
              <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">{job.name || job.job_id}</div>
            </div>
            <div className="space-y-1 text-sm text-[var(--text-muted)]">
              <div>状态：{job.status}</div>
              <div>进度：{Number(job.progress_pct || 0).toFixed(1)}%</div>
              {job.created_at ? <div>创建：{new Date(job.created_at).toLocaleString()}</div> : null}
            </div>
          </div>
        ) : (
          <div className="rounded-[22px] border border-dashed border-[var(--border)] px-4 py-8 text-center text-sm text-[var(--text-dim)]">当前没有活跃任务</div>
        )}

        <div className="space-y-2">
          <div className="text-sm font-semibold text-[var(--text-strong)]">最近任务</div>
          <div className="space-y-2">
            {jobsLoading ? (
              <LoadingHint text="任务列表加载中..." />
            ) : jobs.length > 0 ? (
              jobs.map((j) => (
                <div key={j.job_id} className="rounded-[20px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.72)] px-4 py-3 text-sm">
                  <div className="font-medium text-[var(--text-strong)]">{j.name || j.job_id}</div>
                  <div className="text-[var(--text-dim)]">{j.status} · {Number(j.progress_pct || 0).toFixed(1)}%</div>
                  {j.created_at ? <div className="text-[var(--text-dim)]">{new Date(j.created_at).toLocaleString()}</div> : null}
                </div>
              ))
            ) : (
              <div className="rounded-[20px] border border-dashed border-[var(--border)] px-4 py-6 text-sm text-[var(--text-dim)]">暂无任务</div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
