'use client'

import { useState, useEffect } from 'react'
import axios from 'axios'
import { format } from 'date-fns'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  List,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import dayjs from 'dayjs'
import StockChart from '../components/StockChart'

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

export default function AnalysisPage() {
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [chartData, setChartData] = useState<ChartDataPoint[]>([])
  const [fitResult, setFitResult] = useState<FitResult | null>(null)
  const [cyqInfo, setCyqInfo] = useState<CyqInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [actualStartDate, setActualStartDate] = useState('')
  const [actualEndDate, setActualEndDate] = useState('')

  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [showSearchResults, setShowSearchResults] = useState(false)

  useEffect(() => {
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - 1)
    setEndDate(format(end, 'yyyy-MM-dd'))
    setStartDate(format(start, 'yyyy-MM-dd'))
  }, [])

  const handleSearch = async () => {
    if (!searchKeyword.trim()) {
      setSearchResults([])
      setShowSearchResults(false)
      return
    }

    try {
      setSearchLoading(true)
      const response = await axios.get(`${API_BASE_URL}/api/stock/search`, {
        params: { keyword: searchKeyword.trim() },
      })

      if (response.data.success) {
        setSearchResults(response.data.results)
        setShowSearchResults(true)
      }
    } catch {
      setSearchResults([])
    } finally {
      setSearchLoading(false)
    }
  }

  const handleSelectStock = (stock: StockSearchResult) => {
    setStockCode(stock.code)
    setStockName(stock.name)
    setSearchKeyword('')
    setSearchResults([])
    setShowSearchResults(false)
  }

  const handleFetchData = async () => {
    if (!stockCode.trim()) {
      setError('请输入股票代码')
      return
    }
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
      const hasStockName = !!stockName

      const [stockDataResponse, cyqDataResponse] = await Promise.allSettled([
        axios.get(`${API_BASE_URL}/api/stock/data`, {
          params: {
            symbol: stockCode.trim(),
            start_date: startDate,
            end_date: endDate,
          },
        }),
        axios.get(`${API_BASE_URL}/api/stock/cyq`, {
          params: {
            symbol: stockCode.trim(),
            adjust: '',
          },
        }),
      ])

      if (
        stockDataResponse.status === 'fulfilled' &&
        stockDataResponse.value.data.success
      ) {
        setChartData(stockDataResponse.value.data.chart_data)
        setFitResult(stockDataResponse.value.data.fit_result || null)
        setActualStartDate(stockDataResponse.value.data.actual_start_date || startDate)
        setActualEndDate(stockDataResponse.value.data.actual_end_date || endDate)
        if (!hasStockName) {
          setStockName(stockCode.trim())
        }
      } else {
        const errorMsg =
          stockDataResponse.status === 'rejected'
            ? stockDataResponse.reason?.response?.data?.detail || '获取股票数据失败'
            : '获取股票数据失败'

        if (String(errorMsg).includes('未找到')) {
          setError(`股票代码 ${stockCode} 不存在或数据不可用，请检查代码是否正确`)
        } else {
          setError(errorMsg)
        }
        setChartData([])
        setFitResult(null)
        setActualStartDate('')
        setActualEndDate('')
      }

      if (cyqDataResponse.status === 'fulfilled' && cyqDataResponse.value.data.success) {
        setCyqInfo(cyqDataResponse.value.data.cyq_info)
      } else {
        setCyqInfo(null)
      }
    } catch (err: unknown) {
      const errorMsg =
        typeof err === 'object' && err !== null && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || '获取数据失败'
          : '获取数据失败'
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
    <div className="app-page-shell">
      <div className="app-page-container app-section-stack">
        <Typography.Title level={2} style={{ textAlign: 'center' }}>
          股票分析
        </Typography.Title>

        <Card>
          <Alert
            type="info"
            showIcon
            message="1分钟级数据分析"
            description="系统使用1分钟粒度数据进行价格分布分析"
            style={{ marginBottom: 16 }}
          />

          <Form layout="vertical" onFinish={handleFetchData}>
            <Form.Item label="🔍 搜索股票">
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onPressEnter={handleSearch}
                  placeholder="输入股票代码或名称搜索，如：平安银行 / 000001"
                />
                <Button onClick={handleSearch} loading={searchLoading} disabled={!searchKeyword.trim()}>
                  搜索
                </Button>
              </Space.Compact>
            </Form.Item>

            {showSearchResults && (
              <Card size="small" style={{ marginBottom: 16 }}>
                <List
                  dataSource={searchResults}
                  locale={{ emptyText: '未找到相关股票' }}
                  renderItem={(stock) => (
                    <List.Item onClick={() => handleSelectStock(stock)} style={{ cursor: 'pointer' }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <div>
                          <Typography.Text strong>{stock.name}</Typography.Text>
                          <div>代码: {stock.code}</div>
                        </div>
                        <Tag color="blue">¥{stock.current_price.toFixed(2)}</Tag>
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            )}

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Form.Item label={stockName ? `股票代码（${stockName}）` : '股票代码'} required>
                <Input
                  value={stockCode}
                  onChange={(e) => setStockCode(e.target.value)}
                  onPressEnter={handleFetchData}
                  maxLength={6}
                  placeholder="输入6位股票代码，如：000001"
                />
              </Form.Item>
              <Form.Item label="开始日期" required>
                <DatePicker
                  value={startDate ? dayjs(startDate) : null}
                  onChange={(d) => setStartDate(d ? d.format('YYYY-MM-DD') : '')}
                  style={{ width: '100%' }}
                />
              </Form.Item>
              <Form.Item label="结束日期" required>
                <DatePicker
                  value={endDate ? dayjs(endDate) : null}
                  onChange={(d) => setEndDate(d ? d.format('YYYY-MM-DD') : '')}
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </div>

            <Button type="primary" htmlType="submit" loading={loading} block>
              查询股票数据
            </Button>
          </Form>

          {error && <Alert style={{ marginTop: 16 }} type="error" showIcon message={error} />}
        </Card>

        {chartData.length > 0 && (
          <Card>
            <Space direction="vertical" size={8}>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {stockName || stockCode}
                {stockName && stockCode !== stockName ? (
                  <Typography.Text type="secondary">（{stockCode}）</Typography.Text>
                ) : null}
              </Typography.Title>
              <Space wrap>
                <Tag>数据点数: {chartData.length}</Tag>
                <Tag>粒度: 1分钟</Tag>
                <Tag>请求范围: {startDate} 至 {endDate}</Tag>
                {actualStartDate && actualEndDate ? (
                  <Tag color="blue">实际范围: {actualStartDate} 至 {actualEndDate}</Tag>
                ) : null}
              </Space>
              <Spin spinning={loading}>
                <StockChart data={chartData} period="1min" fitResult={fitResult} cyqInfo={cyqInfo} />
              </Spin>
            </Space>
          </Card>
        )}
      </div>
    </div>
  )
}
