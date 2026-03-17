import { useEffect, useState } from 'react'

export function PaginationControls({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
}: {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const canPrev = page > 1
  const canNext = page < totalPages
  const [jumpPage, setJumpPage] = useState(String(page))

  useEffect(() => {
    setJumpPage(String(page))
  }, [page])

  const submitJump = () => {
    const parsed = Number(jumpPage)
    if (!Number.isFinite(parsed)) return
    const target = Math.min(totalPages, Math.max(1, Math.floor(parsed)))
    if (target !== page) {
      onPageChange(target)
    }
  }

  return (
    <div className="flex items-center justify-between gap-3 text-xs text-gray-600">
      <div>
        共 {total} 条，{page}/{totalPages} 页
      </div>
      <div className="flex items-center gap-2">
        <select
          className="border rounded px-2 py-1"
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
        >
          {[20, 50, 100].map((s) => (
            <option key={s} value={s}>{s}/页</option>
          ))}
        </select>
        <button
          className="px-2 py-1 border rounded disabled:opacity-50"
          disabled={!canPrev}
          onClick={() => onPageChange(page - 1)}
        >上一页</button>
        <button
          className="px-2 py-1 border rounded disabled:opacity-50"
          disabled={!canNext}
          onClick={() => onPageChange(page + 1)}
        >下一页</button>
        <input
          className="w-14 border rounded px-2 py-1"
          value={jumpPage}
          onChange={(e) => setJumpPage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitJump()
          }}
          inputMode="numeric"
        />
        <button className="px-2 py-1 border rounded" onClick={submitJump}>跳转</button>
      </div>
    </div>
  )
}
