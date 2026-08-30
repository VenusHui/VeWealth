export function LoadingHint({ text }: { text: string }) {
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel)] px-4 py-3 text-sm text-[var(--text-dim)]">
      {text}
    </div>
  )
}
