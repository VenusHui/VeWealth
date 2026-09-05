/**
 * Pure status-label data for backtest jobs. Kept free of UI components so it
 * can be unit-tested without pulling in React/antd.
 */
import type { JobStatus } from './components/types'

export const JOB_STATUS_MAP: Record<JobStatus, { label: string; color: string }> = {
  pending: { label: '排队中', color: 'default' },
  running: { label: '运行中', color: 'processing' },
  success: { label: '已完成', color: 'success' },
  completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
}

export const ACTIVE_JOB_STATUSES: readonly string[] = ['pending', 'running']

/**
 * 任务阶段 → 展示标签。后端 job.stage 取值见 job_manager：pending / running /
 * scanning / summarizing / done / failed / cancelled。
 */
export const JOB_STAGE_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: '排队中', color: 'default' },
  running: { label: '运行中', color: 'processing' },
  scanning: { label: '扫描中', color: 'processing' },
  summarizing: { label: '汇总中', color: 'processing' },
  done: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
}

/** 由 job.stage 取展示标签，未知阶段回退到原始值。 */
export function stageLabel(stage?: string | null): string {
  if (!stage) return '—'
  return JOB_STAGE_MAP[stage]?.label ?? stage
}

/** 该阶段是否仍属于进行中（驱动进度条 / 取消按钮）。 */
export function isActiveStage(stage?: string | null): boolean {
  return stage === 'pending' || stage === 'running' || stage === 'scanning' || stage === 'summarizing'
}
