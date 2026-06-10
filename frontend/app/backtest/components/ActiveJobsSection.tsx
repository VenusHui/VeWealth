import { Progress } from 'antd'
import { EmptyState, SurfaceCard } from '../../components/ui-shell'
import { LoadingHint } from './LoadingHint'
import { StatusTag } from './statusLabels'
import type { JobItem } from './types'

export function ActiveJobsSection({
  jobs,
  loading,
}: {
  jobs: JobItem[]
  loading: boolean
}) {
  return (
    <SurfaceCard title="进行中的任务">
      {loading && jobs.length === 0 ? (
        <LoadingHint text="加载中..." />
      ) : jobs.length === 0 ? (
        <EmptyState
          title="暂无进行中的任务"
          description="提交新的回测任务后，进度将显示在这里"
        />
      ) : (
        <div className="space-y-3">
          {jobs.map((j) => {
            const pct = Math.min(Math.max(Number(j.progress_pct || 0), 0), 100)
            return (
              <div
                key={j.job_id}
                className="rounded-[20px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.72)] px-4 py-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-[var(--text-strong)]">
                      {j.name || j.job_id.slice(0, 8)}
                    </div>
                    {j.created_at ? (
                      <div className="text-sm text-[var(--text-dim)]">
                        {new Date(j.created_at).toLocaleString()}
                      </div>
                    ) : null}
                  </div>
                  <StatusTag status={j.status} />
                </div>
                <div className="mt-3">
                  <Progress
                    percent={pct}
                    size="small"
                    showInfo={false}
                    strokeColor={{ '0%': '#0f766e', '100%': '#0d9488' }}
                    trailColor="rgba(15,23,42,0.06)"
                  />
                </div>
              </div>
            )
          })}
        </div>
      )}
    </SurfaceCard>
  )
}
