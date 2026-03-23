export function marketClassByValue(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return 'text-gray-500'
  if (num > 0) return 'text-red-600' // A股：涨红
  if (num < 0) return 'text-green-600' // A股：跌绿
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
