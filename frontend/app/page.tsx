'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { isAuthenticated } from './lib/auth'

export default function HomePage() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  useEffect(() => {
    setIsLoggedIn(isAuthenticated())
  }, [])

  const features = [
    {
      icon: '📊',
      title: '股票分析',
      description: '实时查询A股分时数据，进行价格-成交量分布分析，支持多峰正态分布拟合',
      link: '/analysis',
      color: 'from-blue-500 to-cyan-500'
    },
    {
      icon: '👁️',
      title: '监控列表',
      description: '添加关注的股票到监控列表，系统自动采集并存储历史分时数据',
      link: '/watchlist',
      color: 'from-purple-500 to-pink-500',
      requireAuth: true
    },
    {
      icon: '⚡',
      title: '价格预警',
      description: '当股票价格超过正态分布设定阈值时，通过微信公众号实时通知',
      link: '/watchlist',
      color: 'from-orange-500 to-red-500',
      requireAuth: true
    },
    {
      icon: '📈',
      title: '数据存储',
      description: '突破Akshare 5日限制，长期保存监控股票的分时数据供回溯分析',
      link: '/analysis',
      color: 'from-green-500 to-teal-500'
    }
  ]

  return (
    <div className="app-page-shell">
      <div className="app-page-container-md app-section-stack bg-gradient-to-br from-blue-50 via-indigo-50 to-purple-50 rounded-xl px-4">
      {/* Hero Section */}
      <div className="pt-8 pb-10 px-4">
        <div className="max-w-6xl mx-auto text-center">
          <h1 className="text-6xl font-bold text-gray-800 mb-6">
            💰 VeWealth
          </h1>
          <p className="text-3xl text-gray-600 mb-4">
            A股股票智能分析与监控平台
          </p>
          <p className="text-xl text-gray-500 mb-12">
            基于多峰正态分布的价格分析 · 自动数据采集 · 实时微信预警
          </p>
          
          <div className="flex gap-4 justify-center">
            <Link
              href="/analysis"
              className="px-8 py-4 bg-indigo-600 text-white text-lg font-medium rounded-lg hover:bg-indigo-700 transition-colors shadow-lg"
            >
              开始分析 →
            </Link>
            {!isLoggedIn && (
              <Link
                href="/login"
                className="px-8 py-4 bg-white text-indigo-600 text-lg font-medium rounded-lg hover:bg-gray-50 transition-colors shadow-lg border-2 border-indigo-600"
              >
                登录/注册
              </Link>
            )}
          </div>
        </div>
      </div>

      {/* Features Grid */}
      <div className="pb-10 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center text-gray-800 mb-12">
            核心功能
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {features.map((feature, index) => {
              // 如果需要认证但用户未登录，显示锁定状态
              const isLocked = feature.requireAuth && !isLoggedIn
              
              return (
                <Link
                  key={index}
                  href={isLocked ? '/login' : feature.link}
                  className={`block bg-white rounded-xl shadow-lg overflow-hidden hover:shadow-xl transition-all transform hover:-translate-y-1 ${
                    isLocked ? 'opacity-75' : ''
                  }`}
                >
                  <div className={`h-2 bg-gradient-to-r ${feature.color}`}></div>
                  <div className="p-8">
                    <div className="flex items-start gap-4">
                      <div className="text-5xl">{feature.icon}</div>
                      <div className="flex-1">
                        <h3 className="text-2xl font-bold text-gray-800 mb-3 flex items-center gap-2">
                          {feature.title}
                          {isLocked && (
                            <span className="text-sm bg-yellow-100 text-yellow-700 px-2 py-1 rounded">
                              🔒 需要登录
                            </span>
                          )}
                        </h3>
                        <p className="text-gray-600 leading-relaxed">
                          {feature.description}
                        </p>
                      </div>
                    </div>
                  </div>
                </Link>
              )
            })}
          </div>
        </div>
      </div>
      </div>
    </div>
  )
}
