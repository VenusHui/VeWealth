import { Alert, Collapse, Tag } from 'antd'
import { analyzeFunnel } from '../explain'
import type { Diagnostics } from './types'

/**
 * 任务与结果可解释漏斗卡：展示「股票池 → 数据可用 → 候选 → 入选 → 下单 → 成交」
 * 各级数量与流失原因，并在 0 交易时给出唯一主因与修复建议，warning 可展开。
 *
 * 手动模式（manual_symbols）不记录 diagnostics，届时给出收敛提示而非伪造漏斗。
 */
export function FunnelCard({
  diagnostics,
  warnings,
  title = '漏斗分析',
}: {
  diagnostics?: Diagnostics | null
  warnings?: string[]
  title?: string
}) {
  const analysis = analyzeFunnel(diagnostics)

  if (!analysis.hasDiagnostics) {
    return (
      <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-4 py-3 text-sm text-[var(--text-dim)]">
        {title}：该记录未包含全市场扫描诊断（仅 strategy_select 全市场扫描提供）
      </div>
    )
  }

  const maxCount = Math.max(...analysis.stages.map((s) => s.count), 1)

  return (
    <div className="space-y-4">
      <div className="text-sm font-semibold text-[var(--text-strong)]">{title}</div>

      {analysis.eventCount === 0 && analysis.rootCause ? (
        <Alert
          type="warning"
          showIcon
          message={`0 成交 · ${analysis.rootCause}`}
          description={analysis.fixSuggestion}
        />
      ) : null}

      {analysis.degraded ? (
        <Alert
          type="warning"
          showIcon
          message="行情覆盖不完整，结果已明确降级"
          description="部分标的存在数据覆盖缺口或数据源失败，结论可能受影响。"
        />
      ) : null}

      {/* 漏斗条：每级一行的横向比例条，颜色随流失加深 */}
      <div className="space-y-1.5">
        {analysis.stages.map((stage) => {
          const width = Math.max((stage.count / maxCount) * 100, stage.count > 0 ? 2 : 0)
          return (
            <div key={stage.key}>
              <div className="flex items-center gap-3">
                <div className="w-16 shrink-0 text-xs text-[var(--text-dim)]">{stage.label}</div>
                <div className="relative h-6 flex-1 overflow-hidden rounded-[var(--radius-control)] bg-[var(--surface-subtle)]">
                  <div
                    className="absolute inset-y-0 left-0 rounded-[var(--radius-control)] transition-all"
                    style={{
                      width: `${width}%`,
                      backgroundColor: stage.count > 0 ? 'var(--brand)' : 'var(--border-subtle)',
                    }}
                  />
                </div>
                <div className="w-16 shrink-0 text-right font-semibold tabular-nums text-[var(--text-strong)]">
                  {stage.count.toLocaleString()}
                </div>
              </div>
              {stage.reason ? (
                <div className="flex items-start gap-3 pt-0.5">
                  <div className="w-16 shrink-0" />
                  <div className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
                    <span aria-hidden>↳</span>
                    <span>{stage.reason}</span>
                  </div>
                </div>
              ) : null}
            </div>
          )
        })}
      </div>

      {warnings && warnings.length > 0 ? (
        <Collapse
          size="small"
          items={[
            {
              key: 'warnings',
              label: (
                <span className="text-sm text-[var(--text-muted)]">
                  Warning 提示（{warnings.length}）
                </span>
              ),
              children: (
                <ul className="space-y-1 text-sm text-[var(--text-muted)]">
                  {warnings.map((w, idx) => (
                    <li key={idx} className="flex gap-2">
                      <Tag color="warning" className="shrink-0">
                        提示
                      </Tag>
                      <span className="break-all">{w}</span>
                    </li>
                  ))}
                </ul>
              ),
            },
          ]}
        />
      ) : null}
    </div>
  )
}
