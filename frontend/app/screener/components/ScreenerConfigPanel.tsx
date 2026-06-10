'use client'

import type { Strategy } from '../../backtest/components/types'
import { BOARD_LABELS } from '../../backtest/components/types'

export function ScreenerConfigPanel({
  strategyId,
  strategies,
  selectedStrategy,
  strategyParams,
  boardFilters,
  excludeSt,
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
          选择一个策略，系统将对全市场进行扫描，筛选出当前触发买入信号的标的。
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <label className="ve-field-label">选股策略</label>
          <select className="ve-select" value={strategyId} onChange={(e) => onStrategyChange(e.target.value)}>
            {strategies.map((s) => (
              <option key={s.strategy_id} value={s.strategy_id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {selectedStrategy ? (
        <div className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.68)] p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-[var(--text-strong)]">策略参数</div>
              <div className="text-sm text-[var(--text-dim)]">{selectedStrategy.description}</div>
            </div>
            <div className="ve-info-pill">{selectedStrategy.strategy_id}</div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {selectedStrategy.param_schema.map((p) => (
              <div key={p.key}>
                <label className="ve-field-label">
                  {p.label}
                  {p.min != null && p.max != null ? (
                    <span className="ml-1 text-xs text-[var(--text-dim)]">
                      ({p.min}–{p.max})
                    </span>
                  ) : null}
                </label>
                <input
                  className="ve-input"
                  type={p.type === 'int' || p.type === 'float' ? 'number' : 'text'}
                  step={p.type === 'float' ? '0.01' : '1'}
                  min={p.min}
                  max={p.max}
                  value={strategyParams[p.key] ?? ''}
                  onChange={(e) => onStrategyParamChange(p.key, e.target.value)}
                  placeholder={String(p.default ?? '')}
                />
              </div>
            ))}
          </div>

          <div className="mt-5 rounded-[20px] border border-[var(--border-subtle)] bg-[rgba(248,250,252,0.84)] p-4">
            <div className="text-sm font-semibold text-[var(--text-strong)]">选股范围</div>
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              {(['main', 'gem', 'star', 'bse'] as const).map((key) => (
                <label
                  key={key}
                  className="flex items-center gap-2 rounded-2xl border border-[var(--border-subtle)] bg-white px-3 py-3 text-sm text-[var(--text-muted)] cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={boardFilters.includes(key)}
                    onChange={(e) => onBoardFilterChange(key, e.target.checked)}
                  />
                  <span>{BOARD_LABELS[key]}</span>
                </label>
              ))}
            </div>
            <label className="mt-4 flex items-center gap-2 text-sm text-[var(--text-muted)] cursor-pointer">
              <input
                type="checkbox"
                checked={excludeSt}
                onChange={(e) => onExcludeStChange(e.target.checked)}
              />
              <span>排除 ST / *ST</span>
            </label>
          </div>
        </div>
      ) : null}

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
            ? '正在全市场扫描，请稍候…'
            : '点击后将扫描全市场，筛选触发买入信号的标的。'}
        </span>
      </div>
    </section>
  )
}
