import { Tag } from 'antd'
import type { JobStatus } from './types'

export const JOB_STATUS_MAP: Record<JobStatus, { label: string; color: string }> = {
  pending: { label: '排队中', color: 'default' },
  running: { label: '运行中', color: 'processing' },
  success: { label: '已完成', color: 'success' },
  completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
}

export const ACTIVE_JOB_STATUSES: readonly string[] = ['pending', 'running']

export function StatusTag({ status }: { status: string }) {
  const info = JOB_STATUS_MAP[status as JobStatus] || { label: status, color: 'default' as const }
  return <Tag color={info.color}>{info.label}</Tag>
}
