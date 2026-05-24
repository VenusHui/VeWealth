'use client'

import { Segmented, Switch } from 'antd'

export interface DepthToolbarProps {
  period: string
  onPeriodChange: (period: string) => void
  adjust: string
  onAdjustChange: (adjust: string) => void
  showMA: boolean
  onShowMAToggle: () => void
  showVWAP: boolean
  onShowVWAPToggle: () => void
  showGMM: boolean
  onShowGMMToggle: () => void
  showCYQ: boolean
  onShowCYQToggle: () => void
}

export default function DepthToolbar({
  period,
  onPeriodChange,
  adjust,
  onAdjustChange,
  showMA,
  onShowMAToggle,
  showVWAP,
  onShowVWAPToggle,
  showGMM,
  onShowGMMToggle,
  showCYQ,
  onShowCYQToggle,
}: DepthToolbarProps) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.72)] px-4 py-3">
      {/* Period selector */}
      <div>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">周期</div>
        <Segmented
          size="small"
          value={period}
          onChange={(val) => onPeriodChange(val as string)}
          options={[
            { label: '1分', value: '1min' },
            { label: '5分', value: '5min' },
            { label: '15分', value: '15min' },
            { label: '30分', value: '30min' },
            { label: '60分', value: '60min' },
            { label: '日', value: 'daily' },
          ]}
        />
      </div>

      {/* Adjust type */}
      <div>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.15em] text-[var(--text-dim)]">复权</div>
        <Segmented
          size="small"
          value={adjust}
          onChange={(val) => onAdjustChange(val as string)}
          options={[
            { label: '前复权', value: 'qfq' },
            { label: '后复权', value: 'hfq' },
            { label: '不复权', value: '' },
          ]}
        />
      </div>

      <div className="h-8 w-px bg-[var(--border-subtle)]" />

      {/* Toggle overlays */}
      <div className="flex flex-wrap gap-3">
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <Switch size="small" checked={showMA} onChange={onShowMAToggle} />
          MA
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <Switch size="small" checked={showVWAP} onChange={onShowVWAPToggle} />
          VWAP
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <Switch size="small" checked={showGMM} onChange={onShowGMMToggle} />
          GMM
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <Switch size="small" checked={showCYQ} onChange={onShowCYQToggle} />
          CYQ
        </label>
      </div>
    </div>
  )
}
