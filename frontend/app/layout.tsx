import type { Metadata } from 'next'
import 'antd/dist/reset.css'
import './globals.css'
import Navbar from './components/Navbar'

export const metadata: Metadata = {
  title: 'VeWealth - A股股票分析平台',
  description: '实时A股股票数据查询、分析与监控平台，支持价格预警和微信通知',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN">
      <body>
        <a href="#main-content" className="skip-link">跳到主要内容</a>
        <Navbar />
        <main id="main-content">{children}</main>
      </body>
    </html>
  )
}
