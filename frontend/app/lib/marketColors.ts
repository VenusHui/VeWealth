export function marketClassByValue(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return 'text-gray-500'
  if (num > 0) return 'text-[var(--up)]' // A股：涨红
  if (num < 0) return 'text-[var(--down)]' // A股：跌绿
  return 'text-gray-500'
}

export function marketTagColorByValue(value: unknown): 'red' | 'green' | 'default' {
  const num = Number(value)
  if (!Number.isFinite(num) || num === 0) return 'default'
  return num > 0 ? 'red' : 'green'
}

export function formatPct(value: unknown, digits = 2): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return `${(num * 100).toFixed(digits)}%`
}


export function marketClassByDrawdown(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num) || num === 0) return 'text-gray-500'
  // 回撤按A股红涨绿跌语义展示：回撤(亏损)为绿色
  return 'text-[var(--down)]'
}

export function formatDrawdownPct(value: unknown, digits = 2): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  const absVal = Math.abs(num)
  return `-${(absVal * 100).toFixed(digits)}%`
}

// 倍率字段（如盈亏比 profit_loss_ratio）以倍率展示，而非百分比。后端回传为倍率小数，如 1.5 表示 1.5 倍。
export function formatMultiplier(value: unknown, digits = 2): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return `${num.toFixed(digits)}x`
}
