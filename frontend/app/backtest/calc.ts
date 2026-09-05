/**
 * Pure computation helpers for the backtest panel.
 *
 * Kept free of React/UI concerns so they can be unit-tested in isolation.
 */
import type {
  BoardKey,
  CostConfig,
  SnapshotHolding,
  Strategy,
  StrategyParamField,
  UniverseStats,
} from './components/types'

/**
 * Cast strategy parameter values into their numeric form when they look like
 * numbers, otherwise keep the raw string. Matches the payload the backend
 * expects for strategy params.
 */
export function parseStrategyParams(raw: Record<string, string>): Record<string, unknown> {
  const cast: Record<string, unknown> = {}
  Object.keys(raw).forEach((k) => {
    const val = raw[k]
    cast[k] = /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val
  })
  return cast
}

// ---------------------------------------------------------------------------
// Shared schema-driven strategy config helpers
//
// The backend `param_schema` (VEW-24 contract) carries type/min/max/options and
// defaults, but does NOT yet carry group / unit / help. The frontend keeps a
// small keyed registry to fill those in, so the same parameter is rendered-and
// serialized identically from both the backtest and screener entry points. Once
// the backend exposes group/unit/help explicitly, `resolveParamMeta` prefers the
// backend values and the registry becomes a pure fallback.
// ---------------------------------------------------------------------------

/** 策略参数展示分组：信号（触发条件）与组合（仓位/持有/资金）。 */
export type ParamGroup = 'signal' | 'portfolio'

export type FieldMeta = {
  group: ParamGroup
  unit?: string
  help?: string
}

/**
 * 前端兜底参数元数据。key 取自各策略 param_schema，命中即补充中文帮助/单位。
 * 未命中的参数默认归入「信号」分组且无单位（仅显示 schema 的范围与默认值）。
 */
const STRATEGY_FIELD_META: Record<string, FieldMeta> = {
  short_window: { group: 'signal', unit: '日', help: '短均线周期（快线），用于判断金叉触发。' },
  long_window: { group: 'signal', unit: '日', help: '长均线周期（慢线），必须大于短均线。' },
  lookback_days: { group: 'signal', unit: '日', help: 'GMM 回看窗口天数，用于拟合价格分布。' },
  threshold: { group: 'signal', help: '密度阈值，越高筛选越严格（0.5~0.95）。' },
  max_components: { group: 'signal', unit: '个', help: 'GMM 最大高斯分量数。' },
  refit_interval: { group: 'signal', unit: '日', help: '每多少次交易日后重拟合一次 GMM。' },
  min_price_drop_pct: { group: 'signal', unit: '%', help: '单日最小跌幅（负数），如 -1.0 表示单日下跌 1%。' },
  min_volume_shrink_pct: { group: 'signal', unit: '%', help: '单日最小缩量幅度。' },
  consecutive_days: { group: 'signal', unit: '日', help: '连续满足缩量下跌的天数。' },
  hold_days: { group: 'portfolio', unit: '日', help: '买入后持有天数（卖出规则）。' },
  position_size_pct: { group: 'portfolio', unit: '0~1', help: '单笔仓位占比，0.1 表示 10%。' },
}

/**
 * 仅与组合执行相关、不参与信号计算的参数。选股入口隐藏并剔除这些字段，
 * 避免「按信号产生候选取全市场」的路径带上组合执行参数造成语义混淆。
 */
export const EXECUTION_ONLY_PARAMS = new Set<string>([
  'position_size_pct',
  'max_workers',
  'hold_days',
])

/**
 * 给定入口应渲染参数字段；选股入口剔除仅回测执行相关的参数（与
 * `buildStrategyParams('screener')` 一致），保证「所见即所提交」。
 */
export function visibleParamsForEntry(
  schema: StrategyParamField[] | undefined,
  entry: 'backtest' | 'screener',
): StrategyParamField[] {
  if (!schema) return []
  return schema.filter((p) => entry !== 'screener' || !EXECUTION_ONLY_PARAMS.has(p.key))
}

/** 参数所属分组：backend 显式提供时优先，否则按 key 兜底，默认归「信号」。 */
export function classifyParamGroup(key: string, schemaField?: StrategyParamField): ParamGroup {
  if (schemaField?.group === 'signal' || schemaField?.group === 'portfolio') return schemaField.group
  return STRATEGY_FIELD_META[key]?.group ?? 'signal'
}

/** 解析参数的最终展示元数据（分组/单位/帮助），backend 字段优先于前端兜底表。 */
export function resolveParamMeta(key: string, schemaField?: StrategyParamField): FieldMeta {
  const fallback = STRATEGY_FIELD_META[key] ?? {}
  return {
    group: classifyParamGroup(key, schemaField),
    unit: schemaField?.unit ?? fallback.unit,
    help: schemaField?.help ?? fallback.help,
  }
}

/** ISO 日期字符串（本地时区）。 */
function toISODate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 参考日（含）往前推到的最近一个工作日（忽略法定节假日修正）。 */
function lastWeekday(d: Date): Date {
  const date = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  while (date.getDay() === 0 || date.getDay() === 6) {
    date.setDate(date.getDate() - 1)
  }
  return date
}

/**
 * 默认回测日期区间：结束日为最近一个工作日（日线完整交易日），
 * 起始日为结束日前约一年。替代原先硬编码的 2025 年区间。
 */
export function defaultDateRange(reference: Date = new Date()): { startDate: string; endDate: string } {
  const end = lastWeekday(reference)
  const start = new Date(end)
  start.setDate(start.getDate() - 365)
  return { startDate: toISODate(start), endDate: toISODate(end) }
}

/** 依据 universe stats 与板块/ST 过滤，计算本次实际会扫描的股票数。 */
export function computeStockCount(
  stats: UniverseStats | undefined,
  boards: BoardKey[],
  excludeSt: boolean,
): number {
  if (!stats || boards.length === 0) return 0
  const table = excludeSt ? stats.by_board_exclude_st : stats.by_board
  if (!table) return 0
  return boards.reduce((sum, b) => sum + (table[b] ?? 0), 0)
}

/** 依据扫描股票数估算耗时（秒），取保守值。 */
export function estimateScanDuration(count: number): number {
  if (count <= 0) return 0
  // 经验值：单股取数+评估约 30ms，多路并行取保守折半后的整体估值。
  return Math.max(5, Math.round((count * 30) / 1000))
}

/**
 * 把表单的原始字符串参数转成后端契约参数节点，作为两入口唯一的参数序列化入口：
 * - 按 schema 的 int/float 精确 cast 成 number；非数值参数按「形似数字即转」处理；
 * - `mode === 'screener'` 时剔除仅回测执行相关的参数（position_size_pct/hold_days）；
 * - 空字符串参数置为 undefined 后清理，保证两入口得到的对象逐字段一致。
 */
export function buildStrategyParams(
  strategy: Strategy | undefined,
  raw: Record<string, string>,
  mode: 'manual_symbols' | 'strategy_select' | 'screener',
): Record<string, unknown> {
  const schemaFields = new Map<string, StrategyParamField>()
  strategy?.param_schema.forEach((p) => schemaFields.set(p.key, p))

  const out: Record<string, unknown> = {}
  Object.keys(raw).forEach((key) => {
    if (mode === 'screener' && EXECUTION_ONLY_PARAMS.has(key)) return
    const val = raw[key]
    const field = schemaFields.get(key)
    if (field?.type === 'int' || field?.type === 'float') {
      out[key] = val.trim() === '' ? undefined : Number(val)
      return
    }
    out[key] = /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val
  })

  Object.keys(out).forEach((k) => {
    if (out[k] === undefined) delete out[k]
  })
  return out
}

/** 交易成本摘要文案（用于提交前预览）。 */
export function buildCostSummary(cost: CostConfig): string {
  return `佣金 ${fmtPct(cost.commission_rate)}（最低 ${cost.min_commission.toFixed(2)} 元）+ 印花税 ${fmtPct(
    cost.stamp_tax_rate,
  )}（卖出）+ 滑点 ${fmtPct(cost.slippage_rate)}`
}

function fmtPct(rate: number): string {
  return `${(rate * 100).toFixed(3)}%`
}

/** 近似统计 [startDate, endDate] 内的交易日数（排除周末，不含节假日修正）。 */
export function countTradingDays(startDate: string, endDate: string): number {
  const start = new Date(startDate)
  const end = new Date(endDate)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) return 0
  let count = 0
  for (const d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    const day = d.getDay()
    if (day !== 0 && day !== 6) count++
  }
  return count
}

export type FreshnessCheck = {
  minBars: number
  tradingDays: number
  enoughBars: boolean
  endInFuture: boolean
}

/**
 * 提交前数据新鲜度检查：区间交易天数是否满足策略最小历史 bar 数，
 * 以及结束日是否晚于最近完整交易日（可能尚无数据）。
 */
export function checkDataFreshness(
  startDate: string,
  endDate: string,
  minHistoryBars: number,
  reference: Date = new Date(),
): FreshnessCheck {
  const tradingDays = countTradingDays(startDate, endDate)
  const endInFuture = endDate > toISODate(lastWeekday(reference))
  return {
    minBars: minHistoryBars,
    tradingDays,
    enoughBars: tradingDays >= minHistoryBars,
    endInFuture,
  }
}

export interface StrategyParamValidation {
  valid: boolean
  errors: string[]
}

/**
 * 提交前前端内联校验：类型（非数值）、边界（min/max）、跨字段（short_window <
 * long_window）与被支持的回测模式。与后端 validate_strategy_params 保持同一套规则，
 * 尽量在提交前拦截，避免「合法参数稳定 0 命中」。对非数值/越界参数返回可读错误。
 */
export function validateStrategyParams(
  strategy: Strategy | undefined,
  raw: Record<string, string>,
  mode: string,
): StrategyParamValidation {
  const errors: string[] = []
  if (!strategy) return { valid: true, errors }

  const supported = strategy.supported_modes
  if (Array.isArray(supported) && supported.length > 0 && !supported.includes(mode)) {
    errors.push(`策略「${strategy.name}」不支持当前回测模式`)
  }

  strategy.param_schema.forEach((p) => {
    const val = raw[p.key] ?? ''
    if (p.type !== 'int' && p.type !== 'float') {
      return
    }
    if (val.trim() === '') {
      errors.push(`参数「${p.label}」不能为空`)
      return
    }
    const num = Number(val)
    if (Number.isNaN(num)) {
      errors.push(`参数「${p.label}」必须是数字`)
      return
    }
    if (typeof p.min === 'number' && num < p.min) {
      errors.push(`参数「${p.label}」不能小于 ${p.min}`)
    }
    if (typeof p.max === 'number' && num > p.max) {
      errors.push(`参数「${p.label}」不能大于 ${p.max}`)
    }
  })

  const shortStr = raw.short_window
  const longStr = raw.long_window
  if (shortStr !== undefined && longStr !== undefined && shortStr.trim() !== '' && longStr.trim() !== '') {
    const s = Number(shortStr)
    const l = Number(longStr)
    if (!Number.isNaN(s) && !Number.isNaN(l) && s >= l) {
      errors.push(`short_window(${s}) 必须小于 long_window(${l})`)
    }
  }

  return { valid: errors.length === 0, errors }
}

/** Format a symbol cell: "stockName (code)" when a name is available. */
export function fmtSymbolLabel(symbol?: string, stockName?: string): string {
  const code = symbol || '-'
  if (stockName) return `${stockName} (${code})`
  return code
}

/**
 * Normalize an equity curve to a per-date ratio against the first
 * positive equity value (used for chart comparison across runs).
 */
export function normalizeEquityByDate(
  rows: Array<{ trade_date: string; equity: number | string | null | undefined }>,
): Map<string, number> {
  const map = new Map<string, number>()
  const first = rows.find((x) => Number(x.equity) > 0)
  const base = first ? Number(first.equity) : 0
  for (const p of rows) {
    if (base > 0) map.set(p.trade_date, Number((Number(p.equity) / base).toFixed(6)))
  }
  return map
}

export interface ComparisonChartRow {
  trade_date: string
  main_norm: number | null
  benchmark_norm: number | null
  compare_norm: number | null
}

/**
 * Build the snapshot comparison chart rows by joining the normalized
 * main / benchmark / compare curves on trade date. Rows without a main
 * value are dropped (the chart always plots the main strategy).
 */
export function buildComparisonChartData(
  dates: string[],
  mainByDate: Map<string, number>,
  benchmarkByDate: Map<string, number>,
  compareByDate: Map<string, number>,
): ComparisonChartRow[] {
  return dates
    .map((d) => {
      const mainNorm = mainByDate.get(d)
      const benchmarkNorm = benchmarkByDate.get(d)
      const compareNorm = compareByDate.get(d)
      return {
        trade_date: d,
        main_norm: Number.isFinite(mainNorm as number) ? (mainNorm as number) : null,
        benchmark_norm: Number.isFinite(benchmarkNorm as number) ? (benchmarkNorm as number) : null,
        compare_norm: Number.isFinite(compareNorm as number) ? (compareNorm as number) : null,
      }
    })
    .filter((row) => row.main_norm != null)
}

/** Sort snapshot holdings: closed-today rows last (dimmed in the UI), then by market value desc. */
export function compareSnapshotHoldings(a: SnapshotHolding, b: SnapshotHolding): number {
  const sa = a.position_status === 'closed_today' ? 1 : 0
  const sb = b.position_status === 'closed_today' ? 1 : 0
  if (sa !== sb) return sa - sb
  return Number(b.market_value || 0) - Number(a.market_value || 0)
}

/** Clamp an index into [0, max]. */
export function clampIndex(idx: number, max: number): number {
  return Math.min(Math.max(idx, 0), max)
}

/** Clamp a progress percentage into [0, 100], falling back when falsy. */
export function clampPct(value: number | string | null | undefined, fallback = 0): number {
  const n = Number(value || fallback)
  return Math.min(Math.max(n, 0), 100)
}
