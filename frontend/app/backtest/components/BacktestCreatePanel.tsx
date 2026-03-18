import { LoadingHint } from './LoadingHint'
import type { JobItem, Strategy } from './types'

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
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div className="xl:col-span-2 bg-white rounded-2xl shadow p-5 space-y-4">
        <div className="font-semibold text-gray-800">新建回测任务</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="text-sm">任务名称<input className="mt-1 w-full border rounded-lg px-3 py-2" value={name} onChange={(e) => onNameChange(e.target.value)} /></label>
          <label className="text-sm">策略<select className="mt-1 w-full border rounded-lg px-3 py-2" value={strategyId} onChange={(e) => onStrategyChange(e.target.value)}>{strategies.map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>)}</select></label>
          <label className="text-sm">回测模式<select className="mt-1 w-full border rounded-lg px-3 py-2" value={mode} onChange={(e) => onModeChange(e.target.value as 'manual_symbols' | 'strategy_select')}><option value="manual_symbols">手工股票池</option><option value="strategy_select">策略自动选股</option></select></label>
          {mode === 'manual_symbols' ? (
            <label className="text-sm">股票代码（逗号分隔）<input className="mt-1 w-full border rounded-lg px-3 py-2" value={symbols} onChange={(e) => onSymbolsChange(e.target.value)} /></label>
          ) : (
            <label className="text-sm">选股范围<select className="mt-1 w-full border rounded-lg px-3 py-2" value={universeType} onChange={(e) => onUniverseTypeChange(e.target.value as 'all' | 'custom')}><option value="all">全市场</option><option value="custom">自定义池</option></select></label>
          )}
          {mode === 'strategy_select' && universeType === 'custom' && <label className="text-sm">自定义池<input className="mt-1 w-full border rounded-lg px-3 py-2" value={poolSymbols} onChange={(e) => onPoolSymbolsChange(e.target.value)} /></label>}
          <label className="text-sm">初始资金<input className="mt-1 w-full border rounded-lg px-3 py-2" value={initialCash} onChange={(e) => onInitialCashChange(e.target.value)} /></label>
          <label className="text-sm">开始日期<input type="date" className="mt-1 w-full border rounded-lg px-3 py-2" value={startDate} onChange={(e) => onStartDateChange(e.target.value)} /></label>
          <label className="text-sm">结束日期<input type="date" className="mt-1 w-full border rounded-lg px-3 py-2" value={endDate} onChange={(e) => onEndDateChange(e.target.value)} /></label>
        </div>

        {selectedStrategy && (
          <div className="rounded-xl border bg-gray-50 p-4 space-y-4">
            <div>
              <div className="font-medium mb-3">策略参数</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {selectedStrategy.param_schema.map((p) => (
                  <label key={p.key} className="text-sm">{p.label}<input className="mt-1 w-full border rounded-lg px-3 py-2" value={strategyParams[p.key] ?? ''} onChange={(e) => onStrategyParamChange(p.key, e.target.value)} /></label>
                ))}
              </div>
            </div>

            {mode === 'strategy_select' && (
              <div className="rounded-lg border bg-white p-3">
                <div className="font-medium text-sm mb-2">股票范围过滤</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
                  {([
                    ['main', '主板'],
                    ['gem', '创业板'],
                    ['star', '科创板'],
                    ['bse', '北交所'],
                  ] as const).map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={boardFilters.includes(key)}
                        onChange={(e) => onBoardFilterChange(key, e.target.checked)}
                      />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
                <label className="mt-3 flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={excludeSt} onChange={(e) => onExcludeStChange(e.target.checked)} />
                  <span>排除 ST/*ST</span>
                </label>
              </div>
            )}
          </div>
        )}

        <button onClick={onSubmit} disabled={loading} className="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:bg-gray-400">{loading ? '提交中...' : '提交回测任务'}</button>
        {error && <div className="text-red-600 text-sm">{error}</div>}
      </div>

      <div className="bg-white rounded-2xl shadow p-5 space-y-3">
        <div className="font-semibold text-gray-800">任务状态</div>
        {job && <div className="text-sm rounded-lg bg-indigo-50 p-3">当前任务：{job.job_id}<br/>状态：{job.status} · 进度：{Number(job.progress_pct || 0).toFixed(1)}%</div>}
        <div className="text-sm text-gray-500">最近任务</div>
        <div className="max-h-96 overflow-auto space-y-2">
          {jobsLoading ? (
            <LoadingHint text="任务列表加载中..." />
          ) : jobs.length > 0 ? (
            jobs.map((j) => (
              <div key={j.job_id} className="border rounded-lg px-3 py-2 text-sm">
                <div className="font-medium text-gray-700">{j.job_id}</div>
                <div className="text-gray-500">{j.status} · {Number(j.progress_pct || 0).toFixed(1)}%</div>
              </div>
            ))
          ) : (
            <div className="text-sm text-gray-400">暂无任务</div>
          )}
        </div>
      </div>
    </div>
  )
}
