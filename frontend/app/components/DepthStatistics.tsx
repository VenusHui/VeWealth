'use client'

import { InfoPill, MetricCard } from './ui-shell'
import type { VolumeProfileData } from '../lib/depthChartUtils'

interface DepthStatisticsProps {
  volumeProfile: VolumeProfileData | null
}

export default function DepthStatistics({ volumeProfile }: DepthStatisticsProps) {
  if (!volumeProfile || !volumeProfile.poc || volumeProfile.poc.price === 0) {
    return null
  }

  const { poc, value_area, vwap, total_volume } = volumeProfile
  const vaWidth = value_area.vah - value_area.val

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-6">
      <MetricCard
        label="POC (最大成交量)"
        value={`¥${poc.price.toFixed(2)}`}
        meta={`成交量 ${poc.volume.toLocaleString()}`}
        tone="brand"
        icon="⊙"
      />
      <MetricCard
        label="Value Area High"
        value={`¥${value_area.vah.toFixed(2)}`}
        meta="70% 成交量上界"
        tone="warning"
        icon="△"
      />
      <MetricCard
        label="Value Area Low"
        value={`¥${value_area.val.toFixed(2)}`}
        meta="70% 成交量下界"
        icon="▽"
      />
      <MetricCard
        label="VA 宽度"
        value={`¥${vaWidth.toFixed(2)}`}
        meta={`占比 ${value_area.volume_pct}%`}
        icon="↔"
      />
      <MetricCard
        label="VWAP"
        value={`¥${vwap.toFixed(2)}`}
        meta="成交量加权均价"
        tone="warning"
        icon="◆"
      />
      <MetricCard
        label="总成交量"
        value={total_volume >= 10000 ? `${(total_volume / 10000).toFixed(0)} 万手` : total_volume.toLocaleString()}
        meta="分析周期内合计"
        icon="∿"
      />
    </div>
  )
}
