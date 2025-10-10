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

type PeriodType = '1min' | 'daily'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function Home() {
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [selectedStock, setSelectedStock] = useState<StockSearchResult | null>(null)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [period, setPeriod] = useState<PeriodType>('daily')
  const [chartData, setChartData] = useState<ChartDataPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showResults, setShowResults] = useState(false)

  // 初始化日期（根据周期调整默认范围）
  useEffect(() => {
    const end = new Date()
    const start = new Date()
    
    // 根据不同周期设置默认日期范围
    if (period === 'daily') {
      start.setDate(start.getDate() - 30) // 日线默认30天
    } else {
      start.setDate(start.getDate() - 1) // 分钟线默认1天
    }
    
    setEndDate(format(end, 'yyyy-MM-dd'))
    setStartDate(format(start, 'yyyy-MM-dd'))
  }, [period])

  // 搜索股票
  const handleSearch = async () => {
    if (!searchKeyword.trim()) {
      setSearchResults([])
      return
    }

    try {
      setLoading(true)
      setError('')
      const response = await axios.get(`${API_BASE_URL}/api/stock/search`, {
        params: { keyword: searchKeyword }
      })
      
      if (response.data.success) {
        setSearchResults(response.data.results)
        setShowResults(true)
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || '搜索失败')
    } finally {
      setLoading(false)
    }
  }

  // 选择股票
  const handleSelectStock = (stock: StockSearchResult) => {
    setSelectedStock(stock)
    setShowResults(false)
    setSearchKeyword(`${stock.code} - ${stock.name}`)
  }

  // 获取股票数据
  const handleFetchData = async () => {
    if (!selectedStock) {
      setError('请先选择一只股票')
      return
    }

    if (!startDate || !endDate) {
      setError('请选择日期范围')
      return
    }

    try {
      setLoading(true)
      setError('')
      const response = await axios.get(`${API_BASE_URL}/api/stock/data`, {
        params: {
          symbol: selectedStock.code,
          start_date: startDate,
          end_date: endDate,
          period: period
        }
      })

      if (response.data.success) {
        setChartData(response.data.chart_data)
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || '获取数据失败')
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
          {/* 数据周期选择 */}
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-3">
              数据粒度
            </label>
            <div className="flex gap-3">
              {[
                { 
                  value: '1min', 
                  label: '1分钟数据',
                  description: '最细粒度，适合价格分布分析（支持无限查询，多线程加速）'
                },
                { 
                  value: 'daily', 
                  label: '日线数据',
                  description: '每日收盘数据，适合趋势分析'
                }
              ].map((p) => (
                <button
                  key={p.value}
                  onClick={() => setPeriod(p.value as PeriodType)}
                  className={`flex-1 px-6 py-4 rounded-lg font-medium transition-all text-left ${
                    period === p.value
                      ? 'bg-blue-600 text-white shadow-lg ring-2 ring-blue-300'
                      : 'bg-gray-50 text-gray-700 hover:bg-gray-100 border border-gray-200'
                  }`}
                >
                  <div className="font-bold mb-1">{p.label}</div>
                  <div className={`text-xs ${period === p.value ? 'text-blue-100' : 'text-gray-500'}`}>
                    {p.description}
                  </div>
                </button>
              ))}
            </div>
            {period === '1min' && (
              <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                <p className="text-sm text-green-700">
                  ✅ 1分钟数据已启用多线程加速，可查询任意时间范围
                </p>
                <p className="text-xs text-green-600 mt-1">
                  系统会自动识别交易日，非交易日数据将被过滤
                </p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* 股票搜索 */}
            <div className="relative">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                股票代码/名称
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="输入股票代码或名称"
                  className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <button
                  onClick={handleSearch}
                  disabled={loading}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 transition-colors"
                >
                  搜索
                </button>
              </div>

              {/* 搜索结果下拉 */}
              {showResults && searchResults.length > 0 && (
                <div className="absolute z-10 w-full mt-2 bg-white border border-gray-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
                  {searchResults.map((stock) => (
                    <div
                      key={stock.code}
                      onClick={() => handleSelectStock(stock)}
                      className="px-4 py-2 hover:bg-blue-50 cursor-pointer border-b border-gray-100 last:border-b-0"
                    >
                      <div className="font-medium text-gray-800">
                        {stock.code} - {stock.name}
                      </div>
                      <div className="text-sm text-gray-600">
                        当前价: ¥{stock.current_price.toFixed(2)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
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
              disabled={loading || !selectedStock}
              className="w-full px-6 py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:bg-gray-400 transition-colors"
            >
              {loading ? '加载中...' : '查询股票数据'}
            </button>
          </div>

          {/* 错误信息 */}
          {error && (
            <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
              {error}
            </div>
          )}

          {/* 选中的股票信息 */}
          {selectedStock && (
            <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
              <div className="text-sm text-gray-600">已选择：</div>
              <div className="text-lg font-medium text-gray-800">
                {selectedStock.code} - {selectedStock.name}
              </div>
            </div>
          )}
        </div>

        {/* 图表展示 */}
        {chartData.length > 0 && (
          <div className="bg-white rounded-lg shadow-lg p-6">
            <div className="mb-4">
              <h2 className="text-2xl font-bold text-gray-800">
                {selectedStock?.name} ({selectedStock?.code})
              </h2>
              <div className="mt-2 flex gap-4 text-sm text-gray-600">
                <span>📊 数据点数: {chartData.length}</span>
                <span>📅 {startDate} 至 {endDate}</span>
                <span>⏱️ 周期: {
                  period === '1min' ? '1分钟' : '日线'
                }</span>
              </div>
            </div>
            <StockChart data={chartData} period={period} />
          </div>
        )}
      </div>
    </div>
  )
}

