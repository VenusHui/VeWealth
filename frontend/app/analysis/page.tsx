'use client'

import { useState, useEffect } from 'react'
import axios from 'axios'
import StockChart from '../components/StockChart'
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

interface GaussianComponent {
  mean: number
  std: number
  weight: number
  volume: number
}

interface FitCurvePoint {
  price: number
  fitVolume: number
}

interface FitResult {
  n_components: number
  components: GaussianComponent[]
  fit_curve: FitCurvePoint[]
  bic: number
}

interface CyqInfo {
  date: string
  profit_ratio: number
  avg_cost: number
  cost_90_low: number
  cost_90_high: number
  concentration_90: number
  cost_70_low: number
  cost_70_high: number
  concentration_70: number
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

export default function Home() {
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [chartData, setChartData] = useState<ChartDataPoint[]>([])
  const [fitResult, setFitResult] = useState<FitResult | null>(null)
  const [cyqInfo, setCyqInfo] = useState<CyqInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  
  // 实际数据时间范围
  const [actualStartDate, setActualStartDate] = useState('')
  const [actualEndDate, setActualEndDate] = useState('')
  
  // 搜索相关状态
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [showSearchResults, setShowSearchResults] = useState(false)

  // 初始化日期（默认最近1天）
  useEffect(() => {
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - 1) // 默认查询最近1天
    
    setEndDate(format(end, 'yyyy-MM-dd'))
    setStartDate(format(start, 'yyyy-MM-dd'))
  }, [])

  // 搜索股票
  const handleSearch = async () => {
    if (!searchKeyword.trim()) {
      setSearchResults([])
      setShowSearchResults(false)
      return
    }

    try {
      setSearchLoading(true)
      const response = await axios.get(`${API_BASE_URL}/api/stock/search`, {
        params: {
          keyword: searchKeyword.trim()
        }
      })

      if (response.data.success) {
        setSearchResults(response.data.results)
        setShowSearchResults(true)
      }
    } catch (err: any) {
      console.error('搜索失败:', err)
      setSearchResults([])
    } finally {
      setSearchLoading(false)
    }
  }

  // 选择股票
  const handleSelectStock = (stock: StockSearchResult) => {
    setStockCode(stock.code)
    setStockName(stock.name)
    setSearchKeyword('')
    setSearchResults([])
    setShowSearchResults(false)
  }

  // 获取股票数据和筹码分布
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
      // 如果没有股票名称，则在查询成功后设置为代码
      const hasStockName = !!stockName
      
      // 并行请求股票数据和筹码分布数据
      const [stockDataResponse, cyqDataResponse] = await Promise.allSettled([
        axios.get(`${API_BASE_URL}/api/stock/data`, {
          params: {
            symbol: stockCode.trim(),
            start_date: startDate,
            end_date: endDate
          }
        }),
        axios.get(`${API_BASE_URL}/api/stock/cyq`, {
          params: {
            symbol: stockCode.trim(),
            adjust: '' // 不复权
          }
        })
      ])

      // 处理股票数据响应
      if (stockDataResponse.status === 'fulfilled' && stockDataResponse.value.data.success) {
        setChartData(stockDataResponse.value.data.chart_data)
        setFitResult(stockDataResponse.value.data.fit_result || null)
        setActualStartDate(stockDataResponse.value.data.actual_start_date || startDate)
        setActualEndDate(stockDataResponse.value.data.actual_end_date || endDate)
        if (!hasStockName) {
          setStockName(stockCode.trim())
        }
      } else {
        const errorMsg = stockDataResponse.status === 'rejected' 
          ? (stockDataResponse.reason?.response?.data?.detail || '获取股票数据失败')
          : '获取股票数据失败'
        
        if (errorMsg.includes('未找到')) {
          setError(`股票代码 ${stockCode} 不存在或数据不可用，请检查代码是否正确`)
        } else {
          setError(errorMsg)
        }
        setChartData([])
        setFitResult(null)
        setActualStartDate('')
        setActualEndDate('')
      }

      // 处理筹码分布数据响应
      if (cyqDataResponse.status === 'fulfilled' && cyqDataResponse.value.data.success) {
        setCyqInfo(cyqDataResponse.value.data.cyq_info)
      } else {
        // 筹码分布获取失败不影响主流程，只是设置为null
        setCyqInfo(null)
        console.warn('获取筹码分布数据失败:', cyqDataResponse.status === 'rejected' ? cyqDataResponse.reason : '未知错误')
      }

    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '获取数据失败'
      setError(errorMsg)
      setChartData([])
      setFitResult(null)
      setCyqInfo(null)
      setActualStartDate('')
      setActualEndDate('')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 py-8 px-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-4xl font-bold text-center text-gray-800 mb-8">
          股票分析
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

          {/* 股票搜索区域 */}
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              🔍 搜索股票
            </label>
            <div className="flex gap-2">
              <div className="flex-1 relative">
                <input
                  type="text"
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="输入股票代码或名称搜索，如：平安银行 或 000001"
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
                />
                {searchKeyword && (
                  <button
                    onClick={() => {
                      setSearchKeyword('')
                      setSearchResults([])
                      setShowSearchResults(false)
                    }}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  >
                    ✕
                  </button>
                )}
              </div>
              <button
                onClick={handleSearch}
                disabled={searchLoading || !searchKeyword.trim()}
                className="px-6 py-2 bg-green-600 text-white font-medium rounded-lg hover:bg-green-700 disabled:bg-gray-400 transition-colors"
              >
                {searchLoading ? '搜索中...' : '搜索'}
              </button>
            </div>

            {/* 搜索结果 */}
            {showSearchResults && (
              <div className="mt-2 bg-white border border-gray-300 rounded-lg shadow-lg max-h-60 overflow-y-auto">
                {searchResults.length > 0 ? (
                  <div className="divide-y">
                    {searchResults.map((stock) => (
                      <button
                        key={stock.code}
                        onClick={() => handleSelectStock(stock)}
                        className="w-full px-4 py-3 text-left hover:bg-blue-50 transition-colors flex justify-between items-center"
                      >
                        <div>
                          <div className="font-medium text-gray-800">
                            {stock.name}
                          </div>
                          <div className="text-sm text-gray-500">
                            代码: {stock.code}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm font-medium text-blue-600">
                            ¥{stock.current_price.toFixed(2)}
                          </div>
                          <div className="text-xs text-gray-500">
                            当前价
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="px-4 py-3 text-center text-gray-500">
                    未找到相关股票
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* 股票代码输入 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                股票代码 {stockName && <span className="text-green-600">({stockName})</span>}
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
                或使用上方搜索功能选择股票
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
              ) : '📊 查询股票数据'}
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
        </div>

        {/* 图表展示 */}
        {chartData.length > 0 && (
          <div className="bg-white rounded-lg shadow-lg p-6">
            <div className="mb-4">
              <h2 className="text-2xl font-bold text-gray-800">
                {stockName || stockCode}
                {stockName && stockCode !== stockName && (
                  <span className="text-lg text-gray-500 ml-2">({stockCode})</span>
                )}
              </h2>
              <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
                <span>📊 数据点数: {chartData.length}</span>
                <span>⏱️ 粒度: 1分钟</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-4 text-sm">
                <div className="flex flex-col gap-1">
                  <span className="text-gray-500">📅 请求时间范围:</span>
                  <span className="text-gray-700 font-medium">{startDate} 至 {endDate}</span>
                </div>
                {actualStartDate && actualEndDate && (
                  <div className="flex flex-col gap-1">
                    <span className="text-gray-500">✅ 实际数据范围:</span>
                    <span className="text-green-700 font-medium">{actualStartDate} 至 {actualEndDate}</span>
                  </div>
                )}
              </div>
            </div>
            <StockChart data={chartData} period="1min" fitResult={fitResult} cyqInfo={cyqInfo} />
          </div>
        )}
      </div>
    </div>
  )
}

