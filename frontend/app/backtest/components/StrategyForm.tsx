'use client'

import { useMemo, type ReactNode } from 'react'
import type { BoardKey, CostConfig, Strategy, StrategyParamField, UniverseStats } from './types'
import { BENCHMARK_OPTIONS, BOARD_LABELS, DEFAULT_COST_CONFIG } from './types'
import {
  buildCostSummary,
  checkDataFreshness,
  classifyParamGroup,
  computeStockCount,
  estimateScanDuration,
  resolveParamMeta,
  visibleParamsForEntry,
} from '../calc'

/**
 * 共享 schema-driven 策略配置器。
 *
 * 同时服务「回测」与「选股」两个入口，按 信号 / 股票池 / 组合 / 成交成本 分组，
 * 用同一套参数 schema 渲染字段，保证两入口对相同配置序列化出的参数节点一致，
 * 消除此前两套表单各自实现造成的规则漂移。
 *
 * 纯展示组件：不持有状态，所有值由父组件传入，通过回调上抛变更。
 */
export function StrategyForm({
  entry,
  backtestMode = 'strategy_select',
  strategies,
  strategyId,
  selectedStrategy,
  strategyParams,
  universeType = 'all',
  symbols = '',
  poolSymbols = '',
  boardFilters,
  excludeSt,
  initialCash = '',
  startDate = '',
  endDate = '',
  costConfig = DEFAULT_COST_CONFIG,
  benchmark = '不使用',
  universeStats,
  onStrategyChange,
  onStrategyParamChange,
  onUniverseTypeChange = () => {},
  onSymbolsChange = () => {},
  onPoolSymbolsChange = () => {},
  onBoardFilterChange,
  onExcludeStChange,
  onInitialCashChange = () => {},
  onStartDateChange = () => {},
  onEndDateChange = () => {},
  onCostConfigChange = () => {},
  onBenchmarkChange = () => {},
}: {

  entry: 'backtest' | 'screener'
  /** 回测入口的标的来源模式；选股入口不适用（默认忽略）。 */
  backtestMode?: 'manual_symbols' | 'strategy_select'
  strategies: Strategy[]
  strategyId: string
  selectedStrategy?: Strategy
  strategyParams: Record<string, string>
  universeType?: 'all' | 'custom'
  symbols?: string
  poolSymbols?: string
  boardFilters: BoardKey[]
  excludeSt: boolean
  initialCash?: string
  startDate?: string
  endDate?: string
  costConfig?: CostConfig
  benchmark?: string
  universeStats?: UniverseStats
  onStrategyChange: (v: string) => void
  onStrategyParamChange: (k: string, v: string) => void
  onUniverseTypeChange?: (v: 'all' | 'custom') => void
  onSymbolsChange?: (v: string) => void
  onPoolSymbolsChange?: (v: string) => void
  onBoardFilterChange: (board: BoardKey, checked: boolean) => void
  onExcludeStChange: (v: boolean) => void
  onInitialCashChange?: (v: string) => void
  onStartDateChange?: (v: string) => void
  onEndDateChange?: (v: string) => void
  onCostConfigChange?: (k: keyof CostConfig, v: string) => void
  onBenchmarkChange?: (v: string) => void
}) {
  // 选股入口按 EXECUTION_ONLY_PARAMS 剔除仅回测执行相关的参数，与 buildStrategyParams('screener') 所见即所提交一致。
  const visibleSchema = useMemo(
    () => visibleParamsForEntry(selectedStrategy?.param_schema, entry),
    [selectedStrategy, entry],
  )
  const signalFields = useMemo(
    () => visibleSchema.filter((p) => classifyParamGroup(p.key, p) === 'signal'),
    [visibleSchema],
  )
  const portfolioFields = useMemo(
    () => visibleSchema.filter((p) => classifyParamGroup(p.key, p) === 'portfolio'),
    [visibleSchema],
  )

  const resetGroup = (fields: StrategyParamField[]) => {
    fields.forEach((p) => onStrategyParamChange(p.key, String(p.default ?? '')))
  }
  const resetCost = () => {
    ;(['commission_rate', 'min_commission', 'stamp_tax_rate', 'slippage_rate'] as const).forEach((k) => {
      onCostConfigChange(k, String(DEFAULT_COST_CONFIG[k]))
    })
  }

  const signalResetDisabled = signalFields.every((p) => (strategyParams[p.key] ?? '') === String(p.default ?? ''))
  const portfolioResetDisabled = portfolioFields.every((p) => (strategyParams[p.key] ?? '') === String(p.default ?? ''))

  // ---- pre-submit preview ---- //

  const inputSymbols = symbols.split(',').map((s) => s.trim()).filter(Boolean)
  const inputPoolSymbols = poolSymbols.split(',').map((s) => s.trim()).filter(Boolean)

  const stockCount = useMemo(() => {
    if (entry === 'screener') {
      return computeStockCount(universeStats, boardFilters, excludeSt)
    }
    // backtest
    if (backtestMode === 'manual_symbols') return inputSymbols.length
    if (universeType === 'custom') return inputPoolSymbols.length
    return computeStockCount(universeStats, boardFilters, excludeSt)
  }, [entry, backtestMode, universeType, universeStats, boardFilters, excludeSt, inputSymbols.length, inputPoolSymbols.length])

  const estimatedSec = useMemo(() => estimateScanDuration(stockCount), [stockCount])

  const freshness = useMemo(() => {
    if (entry !== 'backtest' || !selectedStrategy) return null
    return checkDataFreshness(startDate, endDate, selectedStrategy.min_history_bars ?? 0)
  }, [entry, startDate, endDate, selectedStrategy])

  return (
    <div className="space-y-5">
      {/* 策略选择 */}
      <div className="space-y-1">
        <label htmlFor="sf-strategy" className="ve-field-label">策略</label>
        <select id="sf-strategy" className="ve-select" value={strategyId} onChange={(e) => onStrategyChange(e.target.value)}>
          {strategies.map((s) => (
            <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>
          ))}
        </select>
        {selectedStrategy ? <p className="text-xs text-[var(--text-dim)]">{selectedStrategy.description}</p> : null}
      </div>

      {/* 信号 */}
      {selectedStrategy && signalFields.length > 0 ? (
        <FormSection title="信号" description="定义触发买入/卖出信号的策略参数。" onReset={() => resetGroup(signalFields)} resetDisabled={signalResetDisabled}>
          {signalFields.map((p) => (
            <ParamField key={p.key} field={p} value={strategyParams[p.key] ?? ''} onChange={(v) => onStrategyParamChange(p.key, v)} />
          ))}
        </FormSection>
      ) : null}

      {/* 组合（仓位 / 资金 / 区间 / 基准） */}
      {portfolioFields.length > 0 || entry === 'backtest' ? (
        <FormSection
          title="组合"
          description={entry === 'backtest' ? '仓位、资金、回测区间、退出规则与比较基准。' : '仓位与退出规则。'}
          onReset={portfolioFields.length > 0 ? () => resetGroup(portfolioFields) : undefined}
          resetDisabled={portfolioResetDisabled}
        >
          {portfolioFields.map((p) => (
            <ParamField key={p.key} field={p} value={strategyParams[p.key] ?? ''} onChange={(v) => onStrategyParamChange(p.key, v)} />
          ))}
          {entry === 'backtest' ? (
            <>
              <Field label="初始资金" htmlFor="sf-cash">
                <input id="sf-cash" className="ve-input" value={initialCash} onChange={(e) => onInitialCashChange(e.target.value)} placeholder="100000" inputMode="decimal" />
              </Field>
              <Field label="开始日期" htmlFor="sf-start-date">
                <input id="sf-start-date" type="date" className="ve-date-input" value={startDate} onChange={(e) => onStartDateChange(e.target.value)} />
              </Field>
              <Field label="结束日期" htmlFor="sf-end-date">
                <input id="sf-end-date" type="date" className="ve-date-input" value={endDate} onChange={(e) => onEndDateChange(e.target.value)} />
              </Field>
              <Field label="比较基准" htmlFor="sf-benchmark" help="用于回测对比的指数，可选。">
                <select id="sf-benchmark" className="ve-select" value={benchmark} onChange={(e) => onBenchmarkChange(e.target.value)}>
                  <option value="不使用">不使用</option>
                  {BENCHMARK_OPTIONS.map((b) => (
                    <option key={b.value} value={b.value}>{b.label}</option>
                  ))}
                </select>
              </Field>
            </>
          ) : null}
        </FormSection>
      ) : null}

      {/* 股票池 */}
      <FormSection title="股票池" description={entry === 'backtest' ? '定义本次回测覆盖的标的范围。' : '定义本次扫描覆盖的标的范围。'}>
        {entry === 'backtest' && backtestMode === 'manual_symbols' ? (
          <Field label="股票代码（逗号分隔）" htmlFor="sf-symbols">
            <input id="sf-symbols" className="ve-input" value={symbols} onChange={(e) => onSymbolsChange(e.target.value)} placeholder="000001, 600519, 300750" />
          </Field>
        ) : null}

        {entry === 'backtest' && backtestMode === 'strategy_select' ? (
          <Field label="选股范围" htmlFor="sf-universe" help={universeType === 'all' ? '全市场指按下方板块过滤后的全部标的，默认勾选全部板块。' : '仅对自定义股票池中的标的触发策略。'}>
            <div className="space-y-2">
              <label className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-white px-3 py-2 text-sm text-[var(--text-muted)] cursor-pointer">
                <input type="radio" name="sf-universe" checked={universeType === 'all'} onChange={() => onUniverseTypeChange('all')} />
                <span>全市场（按板块过滤）</span>
              </label>
              <label className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-white px-3 py-2 text-sm text-[var(--text-muted)] cursor-pointer">
                <input type="radio" name="sf-universe" checked={universeType === 'custom'} onChange={() => onUniverseTypeChange('custom')} />
                <span>自定义股票池</span>
              </label>
            </div>
          </Field>
        ) : null}

        {entry === 'backtest' && backtestMode === 'strategy_select' && universeType === 'custom' ? (
          <Field label="自定义股票池" htmlFor="sf-pool-symbols">
            <input id="sf-pool-symbols" className="ve-input" value={poolSymbols} onChange={(e) => onPoolSymbolsChange(e.target.value)} placeholder="用逗号分隔自定义股票池" />
          </Field>
        ) : null}

        {entry === 'screener' || (entry === 'backtest' && backtestMode === 'strategy_select' && universeType === 'all') ? (
          <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
            <div className="text-sm font-semibold text-[var(--text-strong)]">板块过滤</div>
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              {(['main', 'gem', 'star', 'bse'] as const).map((key) => (
                <label
                  key={key}
                  className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-white px-3 py-3 text-sm text-[var(--text-muted)] cursor-pointer"
                >
                  <input type="checkbox" checked={boardFilters.includes(key)} onChange={(e) => onBoardFilterChange(key, e.target.checked)} />
                  <span>{BOARD_LABELS[key]}</span>
                </label>
              ))}
            </div>
            <label className="mt-4 flex items-center gap-2 text-sm text-[var(--text-muted)] cursor-pointer">
              <input type="checkbox" checked={excludeSt} onChange={(e) => onExcludeStChange(e.target.checked)} />
              <span>排除 ST / *ST</span>
            </label>
            <p className="mt-3 text-xs text-[var(--text-dim)]">
              「全市场」默认勾选全部板块（主板、创业板、科创板、北交所），可按需缩小范围。
            </p>
          </div>
        ) : entry === 'backtest' && backtestMode === 'manual_symbols' ? (
          <p className="text-xs text-[var(--text-dim)]">手工股票池：仅回测下述代码，不受板块过滤影响。</p>
        ) : null}
      </FormSection>

      {/* 成交成本 */}
      {entry === 'backtest' ? (
        <FormSection title="成交成本" description="回测成交时计入的佣金、印花税与滑点。">
          <CostField label="佣金费率" value={costConfig.commission_rate} max={0.01} step="0.0001" onChange={(v) => onCostConfigChange('commission_rate', v)} />
          <CostField label="最低佣金（元）" value={costConfig.min_commission} max={100} step="0.01" onChange={(v) => onCostConfigChange('min_commission', v)} />
          <CostField label="印花税（仅卖出）" value={costConfig.stamp_tax_rate} max={0.01} step="0.0001" onChange={(v) => onCostConfigChange('stamp_tax_rate', v)} />
          <CostField label="滑点" value={costConfig.slippage_rate} max={0.01} step="0.0001" onChange={(v) => onCostConfigChange('slippage_rate', v)} />
          <div>
            <button type="button" className="ve-button-secondary" onClick={resetCost}>恢复默认成本</button>
          </div>
        </FormSection>
      ) : null}

      {/* 提交前检查 */}
      <SubmitPreview
        entry={entry}
        stockCount={stockCount}
        estimatedSec={estimatedSec}
        startDate={startDate}
        endDate={endDate}
        freshness={freshness}
        costConfig={entry === 'backtest' ? costConfig : undefined}
        hasSymbols={inputSymbols.length > 0}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FormSection({
  title,
  description,
  onReset,
  resetDisabled,
  children,
}: {
  title: string
  description?: string
  onReset?: () => void
  resetDisabled?: boolean
  children: ReactNode
}) {
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-[var(--text-strong)]">{title}</div>
          {description ? <div className="text-xs text-[var(--text-dim)]">{description}</div> : null}
        </div>
        {onReset ? (
          <button type="button" className="ve-button-secondary" onClick={onReset} disabled={resetDisabled}>
            恢复默认
          </button>
        ) : null}
      </div>
      <div className="grid grid-cols-1 gap-4">{children}</div>
    </div>
  )
}

function Field({ label, htmlFor, help, children }: { label: string; htmlFor?: string; help?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="ve-field-label">{label}</label>
      {help ? <p className="mt-1 text-xs text-[var(--text-dim)]">{help}</p> : null}
      <div className={help ? 'mt-1' : undefined}>{children}</div>
    </div>
  )
}

function ParamField({ field, value, onChange }: { field: StrategyParamField; value: string; onChange: (v: string) => void }) {
  const meta = resolveParamMeta(field.key, field)
  const isNumber = field.type === 'int' || field.type === 'float'
  const rangeHint = field.min != null && field.max != null ? `${field.min}–${field.max}${meta.unit ? ` ${meta.unit}` : ''}` : meta.unit

  const control = field.options?.length ? (
    <select id={`sf-param-${field.key}`} className="ve-select" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">请选择</option>
      {field.options.map((opt) => (
        <option key={opt} value={opt}>{opt}</option>
      ))}
    </select>
  ) : isNumber ? (
    <input
      id={`sf-param-${field.key}`}
      className="ve-input"
      type="number"
      step={field.type === 'float' ? '0.01' : '1'}
      min={field.min}
      max={field.max}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={String(field.default ?? '')}
    />
  ) : (
    <input
      id={`sf-param-${field.key}`}
      className="ve-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={String(field.default ?? '')}
    />
  )

  return (
    <div>
      <label htmlFor={`sf-param-${field.key}`} className="ve-field-label">
        {field.label}
        {rangeHint ? <span className="ml-1 text-xs text-[var(--text-dim)]">({rangeHint})</span> : null}
      </label>
      {control}
      {meta.help ? <p className="mt-1 text-xs text-[var(--text-dim)]">{meta.help}</p> : null}
      {field.default != null ? (
        <button type="button" className="mt-1 text-xs text-[var(--brand-strong)] hover:underline" onClick={() => onChange(String(field.default))}>
          推荐预设：{String(field.default)}
        </button>
      ) : null}
    </div>
  )
}

function CostField({ label, value, max, step, onChange }: { label: string; value: number; max: number; step: string; onChange: (v: string) => void }) {
  return (
    <Field label={label}>
      <input className="ve-input" type="number" step={step} min={0} max={max} value={value} onChange={(e) => onChange(e.target.value)} />
      <p className="mt-1 text-xs text-[var(--text-dim)]">范围 0–{max}</p>
    </Field>
  )
}

function SubmitPreview({
  entry,
  stockCount,
  estimatedSec,
  startDate,
  endDate,
  freshness,
  costConfig,
  hasSymbols,
}: {
  entry: 'backtest' | 'screener'
  stockCount: number
  estimatedSec: number
  startDate: string
  endDate: string
  freshness: ReturnType<typeof checkDataFreshness> | null
  costConfig?: CostConfig
  hasSymbols: boolean
}) {
  return (
    <div className="rounded-[var(--radius-card)] border border-[rgba(59,130,246,0.18)] bg-[rgba(239,246,255,0.7)] p-5">
      <div className="mb-3 text-sm font-semibold text-[var(--text-strong)]">提交前检查</div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <PreviewRow label={entry === 'backtest' ? '预计股票数' : '预计扫描数量'} value={`${stockCount.toLocaleString()} 只`} />
        <PreviewRow label="预计耗时" value={estimatedSec > 0 ? `约 ${estimatedSec} 秒` : '—'} />
        {entry === 'backtest' ? (
          <>
            <PreviewRow label="实际日期" value={`${startDate} ~ ${endDate}`} />
            {freshness ? (
              <PreviewRow
                label="数据覆盖"
                value={`约 ${freshness.tradingDays.toLocaleString()} 个交易日`}
                warn={!freshness.enoughBars ? `已选区间约 ${freshness.tradingDays} 个交易日，不足策略要求的 ${freshness.minBars} 根K线，可能导致信号不足。` : freshness.endInFuture ? '结束日晚于最近完整交易日，该段可能尚无行情数据。' : undefined}
              />
            ) : null}
          </>
        ) : (
          <PreviewRow label="数据日期" value="以最近完整交易日为准" />
        )}
        {costConfig ? <PreviewRow label="成本摘要" value={buildCostSummary(costConfig)} wide /> : null}
      </div>
      {entry === 'backtest' && stockCount === 0 && !hasSymbols ? (
        <p className="mt-3 text-xs text-amber-700">当前股票池为空，将不会回测任何标的，请先填写股票代码或选择板块/自定义股票池。</p>
      ) : null}
      {entry === 'screener' && stockCount === 0 ? (
        <p className="mt-3 text-xs text-amber-700">当前未选择任何板块，将不会扫描任何标的。</p>
      ) : null}
    </div>
  )
}

function PreviewRow({ label, value, warn, wide }: { label: string; value: string; warn?: string; wide?: boolean }) {
  return (
    <div className={wide ? 'md:col-span-2' : undefined}>
      <div className="text-xs text-[var(--text-dim)]">{label}</div>
      <div className="text-sm text-[var(--text-strong)]">{value}</div>
      {warn ? <div className="mt-1 text-xs text-amber-700">{warn}</div> : null}
    </div>
  )
}
