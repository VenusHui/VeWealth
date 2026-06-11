import Link from 'next/link'
import type { ReactNode } from 'react'

export function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

export function AppPage({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cx('app-page-shell', className)}>
      <div className="app-page-container">{children}</div>
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  badges,
  align = 'left',
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
  badges?: ReactNode
  align?: 'left' | 'center'
}) {
  return (
    <section
      className={cx(
        've-page-hero',
        align === 'center' && 'text-center items-center',
      )}
    >
      <div className="space-y-4">
        {eyebrow ? <div className="ve-eyebrow">{eyebrow}</div> : null}
        <div className="space-y-3">
          <h1 className="ve-page-title">{title}</h1>
          {description ? <p className="ve-page-description">{description}</p> : null}
        </div>
        {badges ? <div className={cx('flex flex-wrap gap-2', align === 'center' && 'justify-center')}>{badges}</div> : null}
      </div>
      {actions ? <div className={cx('flex flex-wrap gap-3', align === 'center' && 'justify-center')}>{actions}</div> : null}
    </section>
  )
}

export function SurfaceCard({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cx('ve-panel', className)}>
      {title || description || actions ? (
        <header className="mb-5 flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-4 md:flex-row md:items-start md:justify-between">
          <div className="space-y-1">
            {title ? <h2 className="text-lg font-semibold tracking-tight text-[var(--text-strong)]">{title}</h2> : null}
            {description ? <p className="max-w-2xl text-sm leading-6 text-[var(--text-muted)]">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cx('space-y-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function MetricCard({
  label,
  value,
  meta,
  icon,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  meta?: ReactNode
  icon?: ReactNode
  tone?: 'default' | 'brand' | 'positive' | 'warning'
}) {
  return (
    <div className={cx('ve-metric-card', `ve-metric-card--${tone}`)}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-dim)]">{label}</div>
          <div className="text-2xl font-semibold tracking-tight text-[var(--text-strong)] md:text-3xl">{value}</div>
        </div>
        {icon ? <div className="ve-metric-icon">{icon}</div> : null}
      </div>
      {meta ? <div className="text-sm text-[var(--text-muted)]">{meta}</div> : null}
    </div>
  )
}

export function InfoPill({ children }: { children: ReactNode }) {
  return <span className="ve-info-pill">{children}</span>
}

export function CompactStatCard({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  tone?: 'default' | 'brand' | 'positive' | 'negative'
}) {
  const valueColor =
    tone === 'brand' ? 'text-[var(--brand)]' :
    tone === 'positive' ? 'text-red-500' :
    tone === 'negative' ? 'text-green-500' :
    'text-[var(--text-strong)]'
  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2.5 text-center">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-dim)]">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold tabular-nums ${valueColor}`}>{value}</div>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="ve-empty-state">
      <div className="space-y-2">
        <h3 className="text-base font-semibold text-[var(--text-strong)]">{title}</h3>
        {description ? <p className="mx-auto max-w-md text-sm leading-6 text-[var(--text-muted)]">{description}</p> : null}
      </div>
      {action ? <div className="pt-2">{action}</div> : null}
    </div>
  )
}

export function QuickLinkCard({
  href,
  label,
  title,
  description,
  stats,
}: {
  href: string
  label: string
  title: string
  description: string
  stats?: string
}) {
  return (
    <Link href={href} className="ve-quick-link-card group">
      <div className="flex items-center justify-between gap-3">
        <span className="ve-info-pill">{label}</span>
        <span className="text-sm text-[var(--text-dim)] transition-transform duration-200 group-hover:translate-x-1">→</span>
      </div>
      <div className="space-y-2">
        <h3 className="text-xl font-semibold tracking-tight text-[var(--text-strong)]">{title}</h3>
        <p className="text-sm leading-6 text-[var(--text-muted)]">{description}</p>
      </div>
      {stats ? <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-dim)]">{stats}</div> : null}
    </Link>
  )
}
