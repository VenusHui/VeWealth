import { Suspense } from 'react'
import DepthContent from './DepthContent'

export default function DepthPage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-3xl px-4 py-10 text-sm text-[var(--text-dim)]">加载中...</div>}>
      <DepthContent />
    </Suspense>
  )
}
