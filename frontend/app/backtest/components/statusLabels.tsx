import { Tag } from 'antd'
import type { JobStatus } from './types'
import { ACTIVE_JOB_STATUSES, JOB_STATUS_MAP } from '../status'

export { JOB_STATUS_MAP, ACTIVE_JOB_STATUSES }

export function StatusTag({ status }: { status: string }) {
  const info = JOB_STATUS_MAP[status as JobStatus] || { label: status, color: 'default' as const }
  return <Tag color={info.color}>{info.label}</Tag>
}
