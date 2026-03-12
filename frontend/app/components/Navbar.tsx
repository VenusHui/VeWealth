'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useState, useEffect } from 'react'
import { getUser, clearAuth, isAuthenticated } from '../lib/auth'

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
      }
    }
  }, [pathname]) // 路由变化时重新检查

  const handleLogout = () => {
    clearAuth()
    setIsLoggedIn(false)
    setUsername('')
    window.location.href = '/'
  }

  const navLinks = [
    { href: '/', label: '首页', icon: '🏠' },
    { href: '/analysis', label: '股票分析', icon: '📊' },
    { href: '/watchlist', label: '监控列表', icon: '👁️', requireAuth: true },
    { href: '/backtest', label: '策略回测', icon: '📈', requireAuth: true },
  ]

  return (
    <nav className="bg-white shadow-md sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4">
        <div className="flex justify-between items-center h-16">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2">
            <span className="text-2xl">💰</span>
            <span className="text-xl font-bold text-indigo-600">VeWealth</span>
          </Link>

          {/* Navigation Links */}
          <div className="flex items-center gap-6">
            {navLinks.map((link) => {
              // 如果需要认证但用户未登录，不显示该链接
              if (link.requireAuth && !isLoggedIn) return null

              const isActive = pathname === link.href
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`flex items-center gap-1 px-3 py-2 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-indigo-100 text-indigo-700 font-medium'
                      : 'text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  <span>{link.icon}</span>
                  <span>{link.label}</span>
                </Link>
              )
            })}
          </div>

          {/* User Info / Login */}
          <div className="flex items-center gap-3">
            {isLoggedIn ? (
              <>
                <span className="text-sm text-gray-600">
                  👤 {username}
                </span>
                <button
                  onClick={handleLogout}
                  className="px-4 py-2 text-sm bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors"
                >
                  退出
                </button>
              </>
            ) : (
              <Link
                href="/login"
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
              >
                登录/注册
              </Link>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}

