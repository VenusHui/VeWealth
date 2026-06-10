export function LoadingHint({ text }: { text: string }) {
  return (
    <div className="rounded-[20px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.7)] px-4 py-3 text-sm text-[var(--text-dim)]">
      {text}
    </div>
  )
}
