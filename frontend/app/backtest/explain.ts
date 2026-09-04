/**
 * 任务与结果可解释性纯计算：从扫描 diagnostics 推导「股票池 → 数据可用 →
 * 候选 → 入选 → 下单 → 成交」漏斗，并给出 0 交易时的唯一主因与修复建议。
 *
 * 纯函数、不依赖 React/antd，可用 Vitest 单元测试。
 */
import type { Diagnostics } from './components/types'

export interface FunnelStage {
  key: string
  label: string
  count: number
  /** 上一阶段数量；首阶段等于自身（无上级，drop 恒为 0）。 */
  prevCount: number
  /** 相对上一阶段流失的量 = max(prev - count, 0)。 */
  dropped: number
  /** 该阶段流失原因；无流失时为 null。 */
  reason: string | null
}

export interface FunnelAnalysis {
  hasDiagnostics: boolean
  stages: FunnelStage[]
  eventCount: number
  /** 0 交易唯一主因；有成交量或非扫描记录时为 null。 */
  rootCause: string | null
  /** 与 rootCause 对应的修复建议。 */
  fixSuggestion: string | null
  /** 行情覆盖是否降级（data_provenance.degraded）。 */
  degraded: boolean | null
}

const STAGES: Array<{ key: keyof Diagnostics; label: string }> = [
  { key: 'universe_size', label: '股票池' },
  { key: 'data_available_count', label: '数据可用' },
  { key: 'candidate_count', label: '候选' },
  { key: 'selected_count', label: '入选' },
  { key: 'ordered_count', label: '下单' },
  { key: 'event_count', label: '成交' },
]

function countOrZero(val: unknown): number {
  return typeof val === 'number' && Number.isFinite(val) ? val : 0
}

/** 各阶段相对上一阶段的流失原因。 */
function transitionReason(
  key: string,
  dropped: number,
  d: Diagnostics,
): string | null {
  if (dropped <= 0) return null
  switch (key) {
    case 'data_available_count': {
      const empty = countOrZero(d.data_empty_count)
      return empty > 0 ? `${empty} 只无可用数据被跳过` : `有${dropped} 只数据不足/失败被跳过`
    }
    case 'candidate_count':
      return `${dropped} 只股票未产生候选信号`
    case 'selected_count':
      return `${dropped} 条候选未入选`
    case 'ordered_count':
      return `${dropped} 条入选未下单（风险/前置检查过滤）`
    case 'event_count':
      return `${dropped} 笔未能成交（涨停/跌停等执行限制）`
    default:
      return null
  }
}

/** 0 交易主因：漏斗中第一个归零的非首级阶段即为断点；首级为空则池为空。 */
function deriveZeroTradeCause(
  stages: FunnelStage[],
  d: Diagnostics,
): { rootCause: string; fixSuggestion: string } {
  if (stages.length === 0 || stages[0].count === 0) {
    return {
      rootCause: '股票池为空，无法开始扫描',
      fixSuggestion: '检查股票池维表或静态清单是否为空，或改用 custom 股票池手工指定标的',
    }
  }
  const breakStage = stages.slice(1).find((s) => s.count === 0 && s.prevCount > 0)
  if (!breakStage) {
    return {
      rootCause: '未成交（详情见 warnings）',
      fixSuggestion: '展开 warning 与诊断信息，确认是否信号被过滤或执行受限',
    }
  }
  switch (breakStage.key) {
    case 'data_available_count':
      return {
        rootCause: '行情数据缺失或不足，无股票拥有可用日线',
        fixSuggestion: '确认行情数据源覆盖该时间区间，或延长回测区间使日线足够',
      }
    case 'candidate_count':
      return {
        rootCause: '策略未产生任何候选信号',
        fixSuggestion: '放宽策略参数（窗口/阈值），或换用更易出信号的策略',
      }
    case 'selected_count':
      return {
        rootCause: '所有候选均未通过策略筛选（排名/选择规则）',
        fixSuggestion: '调整策略参数或 policy profile 的排名与选择规则',
      }
    case 'ordered_count':
      return {
        rootCause: '已入选标的未进入下单（风险/前置检查过滤）',
        fixSuggestion: '检查风险前置规则、持仓上限与仓位限制',
      }
    case 'event_count':
      return {
        rootCause: '下单均未能成交（涨停/跌停/流动性限制）',
        fixSuggestion: '延长持有周期或调整信号时点，避开涨跌停封板',
      }
    default:
      return {
        rootCause: '未成交',
        fixSuggestion: '查看 warning 与诊断信息定位原因',
      }
  }
}

/**
 * 从 diagnostics 推导漏斗分析与 0 交易主因。diagnostics 为空（manual_symbols
 * 模式不记录）时返回 hasDiagnostics=false，调用方据此隐藏漏斗。
 */
export function analyzeFunnel(diagnostics: Diagnostics | null | undefined): FunnelAnalysis {
  if (!diagnostics || typeof diagnostics !== 'object') {
    return {
      hasDiagnostics: false,
      stages: [],
      eventCount: 0,
      rootCause: null,
      fixSuggestion: null,
      degraded: null,
    }
  }

  const counts = STAGES.map((s) => countOrZero(diagnostics[s.key]))
  const stages: FunnelStage[] = STAGES.map((s, i) => {
    const count = counts[i]
    const prevCount = i === 0 ? count : counts[i - 1]
    const dropped = Math.max(prevCount - count, 0)
    return {
      key: String(s.key),
      label: s.label,
      count,
      prevCount,
      dropped,
      reason: transitionReason(String(s.key), dropped, diagnostics),
    }
  })

  const eventCount = counts[counts.length - 1]
  const degraded = diagnostics.data_provenance?.degraded ?? null
  const zeroCause = eventCount === 0 ? deriveZeroTradeCause(stages, diagnostics) : null

  return {
    hasDiagnostics: true,
    stages,
    eventCount,
    rootCause: zeroCause?.rootCause ?? null,
    fixSuggestion: zeroCause?.fixSuggestion ?? null,
    degraded,
  }
}
