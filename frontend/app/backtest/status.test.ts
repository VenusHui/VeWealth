import { describe, it, expect } from 'vitest'
import { JOB_STATUS_MAP, ACTIVE_JOB_STATUSES } from './status'

describe('JOB_STATUS_MAP', () => {
  it('maps every job status to a display label', () => {
    expect(JOB_STATUS_MAP.pending.label).toBe('排队中')
    expect(JOB_STATUS_MAP.running.label).toBe('运行中')
    expect(JOB_STATUS_MAP.success.label).toBe('已完成')
    expect(JOB_STATUS_MAP.completed.label).toBe('已完成')
    expect(JOB_STATUS_MAP.failed.label).toBe('失败')
    expect(JOB_STATUS_MAP.cancelled.label).toBe('已取消')
  })

  it('assigns a non-default tag color to terminal statuses', () => {
    expect(JOB_STATUS_MAP.success.color).toBe('success')
    expect(JOB_STATUS_MAP.failed.color).toBe('error')
  })
})

describe('ACTIVE_JOB_STATUSES', () => {
  it('contains only in-flight statuses (drives records polling)', () => {
    expect(ACTIVE_JOB_STATUSES).toContain('pending')
    expect(ACTIVE_JOB_STATUSES).toContain('running')
    expect(ACTIVE_JOB_STATUSES).not.toContain('success')
    expect(ACTIVE_JOB_STATUSES).not.toContain('failed')
  })
})
