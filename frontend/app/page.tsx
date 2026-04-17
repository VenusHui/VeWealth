'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { isAuthenticated } from './lib/auth'
import { AppPage, InfoPill, MetricCard, PageHeader, QuickLinkCard, SurfaceCard } from './components/ui-shell'

const capabilityCards = [
  {
    label: '分析台',
    title: '价格分布与筹码结构联动分析',
    description: '把搜索、日期过滤、拟合结果和图表放进一个连续工作流，快速判断成本区与集中成交价。',
    href: '/analysis',
    stats: '1 分钟粒度 · GMM 拟合 · 筹码分布',
  },
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
        eyebrow="Professional A-share workspace"
        title="把 A 股分析、监控与回测，收敛到同一个判断界面。"
        description="VeWealth 现在以更接近交易终端的方式组织信息：顶部统一导航、页面级工作台、克制的卡片层级，以及更明确的筛选 → 判断 → 行动路径。"
        badges={(
          <>
            <InfoPill>实时检索</InfoPill>
            <InfoPill>筹码与价格分布</InfoPill>
            <InfoPill>预警监控</InfoPill>
            <InfoPill>策略回测</InfoPill>
          </>
        )}
        actions={(
          <>
            <Link href="/analysis" className="ve-button-primary">进入分析台</Link>
            <Link href={isLoggedIn ? '/backtest' : '/login'} className="ve-button-secondary">
              {isLoggedIn ? '继续回测' : '登录并开始'}
            </Link>
          </>
        )}
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.5fr_1fr]">
        <SurfaceCard
          title="今日工作流"
          description="按使用频率重新组织入口，避免在功能页之间来回跳转。"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {capabilityCards.map((item) => (
              <QuickLinkCard key={item.href} {...item} href={item.href === '/watchlist' && !isLoggedIn ? '/login' : item.href} />
            ))}
          </div>
        </SurfaceCard>

        <SurfaceCard
          title="界面升级重点"
          description="这次改版优先解决旧版前端信息层次不清、表单与结果区混在一起的问题。"
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-1">
            <MetricCard label="信息结构" value="更清晰" meta="页面先给背景与目标，再给输入区、结果区和后续动作。" tone="brand" icon="▣" />
            <MetricCard label="交互密度" value="更专业" meta="保留数据密度，但用更克制的边框、留白和状态标签来分层。" icon="⇄" />
            <MetricCard label="策略页面" value="可下钻" meta="从任务列表进入详情，再进入策略代码页，形成完整回路。" tone="warning" icon="◎" />
          </div>
        </SurfaceCard>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard label="分析视角" value="价格 × 成交量" meta="把图表容器抬高为主角，而不是附属结果块。" tone="brand" icon="∿" />
        <MetricCard label="监控视角" value="阈值 × 触发" meta="把默认阈值、启用状态与最近预警并置，更像控制台。" icon="⦿" />
        <MetricCard label="回测视角" value="任务 × 记录 × 详情" meta="创建、队列、记录、快照同语义设计，减少上下文切换。" tone="positive" icon="↗" />
      </div>
    </AppPage>
  )
}
