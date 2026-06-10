'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { clearAuth, getUser, isAuthenticated } from '../lib/auth'
import { cx } from './ui-shell'

export default function Navbar() {
  const pathname = usePathname()
  const [username, setUsername] = useState<string>('')
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  useEffect(() => {
    if (isAuthenticated()) {
      const user = getUser()
      if (user) {
        setUsername(user.username)
        setIsLoggedIn(true)
        return
      }
    }
    setIsLoggedIn(false)
    setUsername('')
  }, [pathname])

  const navLinks = useMemo(
    () => [
      { href: '/', label: '首页' },
      { href: '/depth', label: '深度数据' },
      { href: '/screener', label: '选股', requireAuth: true },
      { href: '/watchlist', label: '监控台', requireAuth: true },
      { href: '/alerts', label: '预警', requireAuth: true },
      { href: '/backtest', label: '回测中心', requireAuth: true },
    ],
    [],
  )

  const handleLogout = () => {
    clearAuth()
    setIsLoggedIn(false)
    setUsername('')
    window.location.href = '/'
  }

  return (
    <header className="sticky top-0 z-50 border-b border-[var(--border-subtle)] bg-[rgba(248,250,249,0.82)] backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-3 py-3 md:px-6">
        <Link href="/" className="flex min-w-0 items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-[var(--brand-line)] bg-[rgba(255,255,255,0.88)] text-lg shadow-sm">
            ⌁
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold uppercase tracking-[0.24em] text-[var(--brand-strong)]">VeWealth</div>
            <div className="truncate text-xs text-[var(--text-dim)]">A 股分析 / 监控 / 回测工作台</div>
          </div>
        </Link>

        <nav aria-label="主导航" className="hidden flex-1 justify-center md:flex">
          <div className="flex items-center gap-1 rounded-full border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.7)] p-1.5 shadow-sm">
            {navLinks.map((link) => {
              if (link.requireAuth && !isLoggedIn) return null
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`)
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cx('ve-tab-button', active && 'shadow-sm')}
                  data-active={active}
                >
                  {link.label}
                </Link>
              )
            })}
          </div>
        </nav>

        <div className="flex items-center gap-2 md:gap-3">
          <div className="hidden rounded-full border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.72)] px-3 py-2 text-sm text-[var(--text-muted)] md:block">
            {isLoggedIn ? `交易席位 · ${username}` : '访客模式'}
          </div>
          {isLoggedIn ? (
            <button onClick={handleLogout} className="ve-button-secondary whitespace-nowrap px-4 py-2.5">
              退出
            </button>
          ) : (
            <Link href="/login" className="ve-button-primary whitespace-nowrap px-4 py-2.5">
              登录 / 注册
            </Link>
          )}
        </div>
      </div>

      <div className="border-t border-[var(--border-subtle)] px-3 py-2 md:hidden">
        <nav aria-label="移动端主导航" className="flex gap-2 overflow-x-auto">
          {navLinks.map((link) => {
            if (link.requireAuth && !isLoggedIn) return null
            const active = pathname === link.href || pathname.startsWith(`${link.href}/`)
            return (
              <Link key={link.href} href={link.href} className="ve-tab-button whitespace-nowrap" data-active={active}>
                {link.label}
              </Link>
            )
          })}
        </nav>
      </div>
    </header>
  )
}
