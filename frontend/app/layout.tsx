import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'A股股票平台',
  description: '实时A股股票数据查询与分析平台',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  )
}

