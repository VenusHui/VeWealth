import type { CostConfig, Strategy, UniverseStats } from './types'
import { StrategyForm } from './StrategyForm'

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
  boardFilters,
  excludeSt,
  costConfig,
  benchmark,
  universeStats,
  loading,
  error,
  onNameChange,
  onStrategyChange,
  onModeChange,
  onUniverseTypeChange,
  onSymbolsChange,
  onPoolSymbolsChange,
  onInitialCashChange,
  onStartDateChange,
  onEndDateChange,
  onBoardFilterChange,
  onExcludeStChange,
  onStrategyParamChange,
  onCostConfigChange,
  onBenchmarkChange,
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
  boardFilters: Array<'main' | 'gem' | 'star' | 'bse'>
  excludeSt: boolean
  costConfig: CostConfig
  benchmark: string
  universeStats?: UniverseStats
  loading: boolean
  error: string
  onNameChange: (v: string) => void
  onStrategyChange: (v: string) => void
  onModeChange: (v: 'manual_symbols' | 'strategy_select') => void
  onUniverseTypeChange: (v: 'all' | 'custom') => void
  onSymbolsChange: (v: string) => void
  onPoolSymbolsChange: (v: string) => void
  onInitialCashChange: (v: string) => void
  onStartDateChange: (v: string) => void
  onEndDateChange: (v: string) => void
  onBoardFilterChange: (board: 'main' | 'gem' | 'star' | 'bse', checked: boolean) => void
  onExcludeStChange: (v: boolean) => void
  onStrategyParamChange: (k: string, v: string) => void
  onCostConfigChange: (k: keyof CostConfig, v: string) => void
  onBenchmarkChange: (v: string) => void
  onSubmit: () => void
}) {
  const isModeSupported = (m: 'manual_symbols' | 'strategy_select') =>
    !selectedStrategy ||
    !Array.isArray(selectedStrategy.supported_modes) ||
    selectedStrategy.supported_modes.length === 0 ||
    selectedStrategy.supported_modes.includes(m)

  return (
    <section className="ve-panel space-y-5">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">新建回测任务</h2>
      </div>

      <div>
        <label htmlFor="bt-task-name" className="ve-field-label">任务名称</label>
        <input id="bt-task-name" className="ve-input" value={name} onChange={(e) => onNameChange(e.target.value)} placeholder="例如：2025 主板趋势轮动" />
      </div>

      <div>
        <label htmlFor="bt-mode" className="ve-field-label">回测模式</label>
        <select id="bt-mode" className="ve-select" value={mode} onChange={(e) => onModeChange(e.target.value as 'manual_symbols' | 'strategy_select')}>
          <option value="manual_symbols" disabled={!isModeSupported('manual_symbols')}>手工股票池</option>
          <option value="strategy_select" disabled={!isModeSupported('strategy_select')}>策略自动选股</option>
        </select>
        <p className="mt-1 text-xs text-[var(--text-dim)]">模式只决定股票池来源，不改变成交语义；两者使用同一套回测引擎。</p>
      </div>

      <StrategyForm
        entry="backtest"
        backtestMode={mode}
        strategies={strategies}
        strategyId={strategyId}
        selectedStrategy={selectedStrategy}
        strategyParams={strategyParams}
        universeType={universeType}
        symbols={symbols}
        poolSymbols={poolSymbols}
        boardFilters={boardFilters}
        excludeSt={excludeSt}
        initialCash={initialCash}
        startDate={startDate}
        endDate={endDate}
        costConfig={costConfig}
        benchmark={benchmark}
        universeStats={universeStats}
        onStrategyChange={onStrategyChange}
        onStrategyParamChange={onStrategyParamChange}
        onUniverseTypeChange={onUniverseTypeChange}
        onSymbolsChange={onSymbolsChange}
        onPoolSymbolsChange={onPoolSymbolsChange}
        onBoardFilterChange={onBoardFilterChange}
        onExcludeStChange={onExcludeStChange}
        onInitialCashChange={onInitialCashChange}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
        onCostConfigChange={onCostConfigChange}
        onBenchmarkChange={onBenchmarkChange}
      />

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onSubmit} disabled={loading} className="ve-button-primary">
          {loading ? '提交中…' : '提交回测任务'}
        </button>
        <span className="text-sm text-[var(--text-dim)]">提交后将跳转至记录页面，可实时查看任务进度。</span>
      </div>

      {error ? <div className="rounded-[var(--radius-card)] border border-[rgba(220,38,38,0.16)] bg-[rgba(254,242,242,0.86)] px-4 py-3 text-sm text-red-700">{error}</div> : null}
    </section>
  )
}
