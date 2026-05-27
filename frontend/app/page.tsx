'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { isAuthenticated } from './lib/auth'
import { AppPage, InfoPill, PageHeader, QuickLinkCard } from './components/ui-shell'

const capabilityCards = [
  {
    label: '监控台',
    title: '把关注标的沉淀成可执行的预警清单',
    description: '监控列表、阈值控制和最近告警在一处统一查看，降低重复操作。',
    href: '/watchlist',
    stats: '阈值管理 · 告警开关 · 持续跟踪',
  },
  {
    label: '回测中心',
    title: '从策略配置到结果钻取的完整闭环',
    description: '创建任务、查看运行状态、下钻成交与快照，并进入策略管理页追溯代码。',
    href: '/backtest',
    stats: '任务队列 · 交易明细 · 策略配置',
  },
]

export default function HomePage() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  useEffect(() => {
    setIsLoggedIn(isAuthenticated())
  }, [])

  return (
    <AppPage>
      <PageHeader
        eyebrow="A-share workspace"
        title="分析、监控与回测，一个工作台完成。"
        badges={(
          <>
            <InfoPill>价格分布</InfoPill>
            <InfoPill>预警监控</InfoPill>
            <InfoPill>策略回测</InfoPill>
          </>
        )}
        actions={(
          <Link href={isLoggedIn ? '/backtest' : '/login'} className="ve-button-primary">
            {isLoggedIn ? '回测中心' : '登录'}
          </Link>
        )}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {capabilityCards.map((item) => (
          <QuickLinkCard key={item.href} {...item} href={item.href === '/watchlist' && !isLoggedIn ? '/login' : item.href} />
        ))}
      </div>
    </AppPage>
  )
}
