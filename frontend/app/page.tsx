'use client'

import { useState, useEffect } from 'react'
import axios from 'axios'
import StockChart from './components/StockChart'
import { format } from 'date-fns'

interface StockSearchResult {
  code: string
  name: string
  current_price: number
}

interface ChartDataPoint {
  datetime: string
  price: number
  volume: number
  open?: number
  high?: number
  low?: number
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function Home() {
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [chartData, setChartData] = useState<ChartDataPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 初始化日期（默认最近1天）
  useEffect(() => {
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - 1) // 默认查询最近1天
    
    setEndDate(format(end, 'yyyy-MM-dd'))
    setStartDate(format(start, 'yyyy-MM-dd'))
  }, [])

  // 获取股票数据
  const handleFetchData = async () => {
    // 验证股票代码
    if (!stockCode.trim()) {
      setError('请输入股票代码')
      return
    }

    // 验证股票代码格式（6位数字）
    if (!/^\d{6}$/.test(stockCode.trim())) {
      setError('股票代码格式错误，应为6位数字（如：000001）')
      return
    }

    if (!startDate || !endDate) {
      setError('请选择日期范围')
      return
    }

    try {
      setLoading(true)
      setError('')
      setStockName('') // 清空之前的股票名称
      
      const response = await axios.get(`${API_BASE_URL}/api/stock/data`, {
        params: {
          symbol: stockCode.trim(),
          start_date: startDate,
          end_date: endDate
        }
      })

      if (response.data.success) {
        setChartData(response.data.chart_data)
        // 查询成功后，可以尝试获取股票名称
        setStockName(stockCode.trim())
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '获取数据失败'
      if (errorMsg.includes('未找到')) {
        setError(`股票代码 ${stockCode} 不存在或数据不可用，请检查代码是否正确`)
      } else {
        setError(errorMsg)
      }
      setChartData([]) // 清空图表数据
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 py-8 px-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-4xl font-bold text-center text-gray-800 mb-8">
          A股股票分析平台
        </h1>

        <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
          {/* 数据说明 */}
          <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
            <div className="flex items-start gap-3">
              <span className="text-2xl">📊</span>
              <div>
                <h3 className="font-bold text-blue-900 mb-1">1分钟级数据分析</h3>
                <p className="text-sm text-blue-700">
                  系统使用1分钟粒度数据进行价格分布分析
                </p>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* 股票代码输入 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                股票代码
              </label>
              <input
                type="text"
                value={stockCode}
                onChange={(e) => setStockCode(e.target.value)}
                onKeyPress={(e) => e.key === 'Enter' && handleFetchData()}
                placeholder="输入6位股票代码，如：000001"
                maxLength={6}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <p className="mt-1 text-xs text-gray-500">
                示例：000001（平安银行）、600519（贵州茅台）
              </p>
            </div>

            {/* 开始日期 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                开始日期
              </label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            {/* 结束日期 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                结束日期
              </label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>

          {/* 查询按钮 */}
          <div className="mt-4">
            <button
              onClick={handleFetchData}
              disabled={loading || !stockCode.trim()}
              className="w-full px-6 py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:bg-gray-400 transition-colors shadow-md"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                  </svg>
                  加载中...
                </span>
              ) : '查询股票数据'}
            </button>
          </div>

          {/* 错误信息 */}
          {error && (
            <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg">
              <div className="flex items-start gap-2">
                <span className="text-red-500 text-lg">⚠️</span>
                <div className="flex-1 text-red-700">{error}</div>
              </div>
            </div>
          )}

          {/* 当前查询的股票信息 */}
          {stockName && chartData.length > 0 && (
            <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
              <div className="text-sm text-gray-600">当前股票：</div>
              <div className="text-lg font-medium text-gray-800">
                {stockName}
              </div>
            </div>
          )}
        </div>

        {/* 图表展示 */}
        {chartData.length > 0 && (
          <div className="bg-white rounded-lg shadow-lg p-6">
            <div className="mb-4">
              <h2 className="text-2xl font-bold text-gray-800">
                股票代码: {stockName}
              </h2>
              <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
                <span>📊 数据点数: {chartData.length}</span>
                <span>📅 {startDate} 至 {endDate}</span>
                <span>⏱️ 粒度: 1分钟</span>
              </div>
            </div>
            <StockChart data={chartData} period="1min" />
          </div>
        )}
      </div>
    </div>
  )
}

