'use client'

import type { Strategy, UniverseStats } from '../../backtest/components/types'
import { StrategyForm } from '../../backtest/components/StrategyForm'

/**
 * 策略选股配置面板，复用共享的 schema-driven StrategyForm。
 *
 * 与回测入口共用同一套参数 schema 渲染与序列化逻辑，仅展示「信号 + 股票池」分组，
 * 剔除仅回测执行相关的参数，保证两入口对相同配置产出一致的信号参数。
 */
export function ScreenerConfigPanel({
  strategyId,
  strategies,
  selectedStrategy,
  strategyParams,
  boardFilters,
  excludeSt,
  universeStats,
  scanning,
  onStrategyChange,
  onBoardFilterChange,
  onExcludeStChange,
  onStrategyParamChange,
  onStartScan,
}: {
  strategyId: string
  strategies: Strategy[]
  selectedStrategy?: Strategy
  strategyParams: Record<string, string>
  boardFilters: Array<'main' | 'gem' | 'star' | 'bse'>
  excludeSt: boolean
  universeStats?: UniverseStats
  scanning: boolean
  onStrategyChange: (v: string) => void
  onBoardFilterChange: (board: 'main' | 'gem' | 'star' | 'bse', checked: boolean) => void
  onExcludeStChange: (v: boolean) => void
  onStrategyParamChange: (k: string, v: string) => void
  onStartScan: () => void
}) {
  const canStart = strategyId && boardFilters.length > 0

  return (
    <section className="ve-panel space-y-5">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">策略选股配置</h2>
        <p className="text-sm text-[var(--text-muted)]">
          选择一个策略，系统将对选定的股票池扫描，筛选出触发买入信号的标的。
        </p>
      </div>

      <StrategyForm
        entry="screener"
        strategies={strategies}
        strategyId={strategyId}
        selectedStrategy={selectedStrategy}
        strategyParams={strategyParams}
        boardFilters={boardFilters}
        excludeSt={excludeSt}
        universeStats={universeStats}
        onStrategyChange={onStrategyChange}
        onStrategyParamChange={onStrategyParamChange}
        onBoardFilterChange={onBoardFilterChange}
        onExcludeStChange={onExcludeStChange}
      />

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onStartScan}
          disabled={!canStart || scanning}
          className="ve-button-primary"
        >
          {scanning ? '扫描中…' : '开始选股'}
        </button>
        <span className="text-sm text-[var(--text-dim)]">
          {scanning
            ? '正在扫描，请稍候…'
            : '点击后将扫描选定股票池，筛选触发买入信号的标的。'}
        </span>
      </div>
    </section>
  )
}
