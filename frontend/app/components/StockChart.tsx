'use client'

import { useMemo, useState } from 'react'
import { 
  ComposedChart,
  Bar,
  Line,
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  Legend,
  ReferenceLine
} from 'recharts'

interface ChartDataPoint {
  datetime: string
  price: number
  volume: number
  open?: number
  high?: number
  low?: number
}

interface StockChartProps {
  data: ChartDataPoint[]
  period: string
}

interface AggregatedDataPoint {
  price: number
  totalVolume: number
  binVolume: number
  count: number
  percentage: number
  binPercentage: number
}

export default function StockChart({ data, period }: StockChartProps) {
  // 控制图表显示/隐藏
  const [showBar, setShowBar] = useState(true)
  const [showLine, setShowLine] = useState(true)

  // 聚合相同价格的成交量，并计算区间成交量
  const aggregatedData = useMemo(() => {
    if (!data || data.length === 0) return []

    // 1. 按具体价格分组并累加成交量
    const priceMap = new Map<number, { volume: number; count: number }>()
    
    data.forEach(point => {
      // 直接使用具体价格值（保留2位小数）
      const price = Number(point.price.toFixed(2))
      const existing = priceMap.get(price) || { volume: 0, count: 0 }
      priceMap.set(price, {
        volume: existing.volume + point.volume,
        count: existing.count + 1
      })
    })

    // 2. 转换为数组并排序
    const priceData = Array.from(priceMap.entries())
      .map(([price, data]) => ({
        price: price,
        totalVolume: data.volume,
        count: data.count,
      }))
      .sort((a, b) => a.price - b.price)

    if (priceData.length === 0) return []

    // 3. 计算价格区间（用于折线图）
    const prices = priceData.map(d => d.price)
    const minPrice = Math.min(...prices)
    const maxPrice = Math.max(...prices)
    const priceRange = maxPrice - minPrice

    // 动态确定价格区间大小（分成约50个区间）
    const numBins = Math.min(50, priceData.length)
    const binSize = priceRange / numBins || 0.01

    // 4. 为每个价格点计算其所属区间的总成交量
    const binMap = new Map<number, number>()
    
    priceData.forEach(point => {
      const binPrice = Math.floor(point.price / binSize) * binSize
      const existing = binMap.get(binPrice) || 0
      binMap.set(binPrice, existing + point.totalVolume)
    })

    // 5. 合并数据
    const result = priceData.map(point => {
      const binPrice = Math.floor(point.price / binSize) * binSize
      const binVolume = binMap.get(binPrice) || 0
      
      return {
        price: point.price,
        totalVolume: point.totalVolume,
        binVolume: binVolume,
        count: point.count,
        percentage: 0,
        binPercentage: 0
      }
    })

    // 6. 计算百分比
    const totalVolume = result.reduce((sum, item) => sum + item.totalVolume, 0)
    const totalBinVolume = Array.from(binMap.values()).reduce((sum, vol) => sum + vol, 0)
    
    result.forEach(item => {
      item.percentage = (item.totalVolume / totalVolume) * 100
      item.binPercentage = (item.binVolume / totalBinVolume) * 100
    })

    return result
  }, [data])

  // 找出成交量最大的价格区间（峰值）
  const peakData = useMemo(() => {
    if (aggregatedData.length === 0) return null
    return aggregatedData.reduce((max, current) => 
      current.totalVolume > max.totalVolume ? current : max
    )
  }, [aggregatedData])

  // 计算统计信息
  const statistics = useMemo(() => {
    if (aggregatedData.length === 0) return null

    const totalVolume = aggregatedData.reduce((sum, item) => sum + item.totalVolume, 0)
    const weightedPriceSum = aggregatedData.reduce((sum, item) => 
      sum + item.price * item.totalVolume, 0
    )
    const meanPrice = weightedPriceSum / totalVolume

    // 计算标准差
    const variance = aggregatedData.reduce((sum, item) => {
      const diff = item.price - meanPrice
      return sum + diff * diff * item.totalVolume
    }, 0) / totalVolume
    const stdDev = Math.sqrt(variance)

    return { meanPrice, stdDev }
  }, [aggregatedData])

  // 自定义Tooltip
  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload as AggregatedDataPoint
      return (
        <div className="bg-white p-3 border border-gray-300 rounded-lg shadow-lg">
          <p className="text-sm font-semibold text-gray-800 mb-2">
            价格: ¥{data.price.toFixed(2)}
          </p>
          <div className="space-y-1">
            <p className="text-sm text-blue-600 font-semibold">
              该价格成交量: {data.totalVolume >= 100000000 
                ? `${(data.totalVolume / 100000000).toFixed(2)}亿` 
                : data.totalVolume >= 10000 
                  ? `${(data.totalVolume / 10000).toFixed(0)}万`
                  : data.totalVolume.toLocaleString()}
            </p>
            <p className="text-sm text-orange-600 font-semibold">
              区间成交量: {data.binVolume >= 100000000 
                ? `${(data.binVolume / 100000000).toFixed(2)}亿` 
                : data.binVolume >= 10000 
                  ? `${(data.binVolume / 10000).toFixed(0)}万`
                  : data.binVolume.toLocaleString()}
            </p>
            <div className="border-t border-gray-200 my-1"></div>
            <p className="text-xs text-gray-600">
              该价格占比: {data.percentage.toFixed(2)}%
            </p>
            <p className="text-xs text-gray-600">
              交易次数: {data.count}
            </p>
          </div>
        </div>
      )
    }
    return null
  }

  // 格式化成交量
  const formatVolume = (value: number) => {
    if (value >= 100000000) {
      return `${(value / 100000000).toFixed(1)}亿`
    } else if (value >= 10000) {
      return `${(value / 10000).toFixed(0)}万`
    }
    return value.toString()
  }

  return (
    <div className="w-full">
      {/* 统计信息 */}
      {statistics && peakData && (
        <div className="mb-4 grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-blue-50 p-4 rounded-lg border border-blue-200">
            <div className="text-sm text-gray-600">成交集中价位</div>
            <div className="text-2xl font-bold text-blue-600">
              ¥{peakData.price.toFixed(2)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              占总成交量 {peakData.percentage.toFixed(2)}%
            </div>
          </div>
          <div className="bg-green-50 p-4 rounded-lg border border-green-200">
            <div className="text-sm text-gray-600">加权平均价</div>
            <div className="text-2xl font-bold text-green-600">
              ¥{statistics.meanPrice.toFixed(2)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              成交量加权计算
            </div>
          </div>
          <div className="bg-purple-50 p-4 rounded-lg border border-purple-200">
            <div className="text-sm text-gray-600">价格波动范围</div>
            <div className="text-2xl font-bold text-purple-600">
              ±¥{statistics.stdDev.toFixed(2)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              标准差（σ）
            </div>
          </div>
        </div>
      )}

      {/* 价格分布图 */}
      <div className="h-[500px] bg-white rounded-lg p-4 border border-gray-200">
        <div className="flex justify-between items-center mb-2">
          <h3 className="text-lg font-semibold text-gray-700">
            价格-成交量分布图
          </h3>
          <div className="flex gap-2">
            <button
              onClick={() => setShowBar(!showBar)}
              className={`px-3 py-1 rounded-md text-sm font-medium transition-colors ${
                showBar
                  ? 'bg-blue-500 text-white hover:bg-blue-600'
                  : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
              }`}
            >
              {showBar ? '✓' : ''} 柱状图
            </button>
            <button
              onClick={() => setShowLine(!showLine)}
              className={`px-3 py-1 rounded-md text-sm font-medium transition-colors ${
                showLine
                  ? 'bg-orange-500 text-white hover:bg-orange-600'
                  : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
              }`}
            >
              {showLine ? '✓' : ''} 折线图
            </button>
          </div>
        </div>
        <ResponsiveContainer width="100%" height="90%">
          <ComposedChart
            data={aggregatedData}
            margin={{
              top: 20,
              right: 30,
              left: 60,
              bottom: 60,
            }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
            <XAxis 
              type="number"
              dataKey="price" 
              name="价格"
              label={{ 
                value: '价格 (¥)', 
                position: 'insideBottom', 
                offset: -15,
                style: { fontSize: '14px', fontWeight: 'bold' }
              }}
              tick={{ fontSize: 12 }}
              domain={['dataMin', 'dataMax']}
              tickFormatter={(value) => `¥${value.toFixed(2)}`}
            />
            <YAxis 
              type="number"
              name="成交量"
              label={{ 
                value: '成交量', 
                angle: -90, 
                position: 'insideLeft',
                style: { fontSize: '14px', fontWeight: 'bold' }
              }}
              tick={{ fontSize: 12 }}
              tickFormatter={formatVolume}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(59, 130, 246, 0.1)' }} />
            <Legend 
              verticalAlign="top" 
              height={36}
              wrapperStyle={{ fontSize: '14px' }}
            />
            
            {/* 加权平均价参考线 */}
            {statistics && (
              <ReferenceLine 
                x={statistics.meanPrice} 
                stroke="#10b981" 
                strokeDasharray="5 5"
                label={{ value: '平均价', position: 'top', fill: '#10b981', fontSize: 12 }}
              />
            )}
            
            {/* 峰值参考线 */}
            {peakData && (
              <ReferenceLine 
                x={peakData.price} 
                stroke="#ef4444" 
                strokeDasharray="5 5"
                label={{ value: '集中价位', position: 'top', fill: '#ef4444', fontSize: 12 }}
              />
            )}
            
            {/* 柱状图：具体价格成交量 */}
            {showBar && (
              <Bar 
                dataKey="totalVolume"
                name="该价格成交量"
                fill="#3b82f6"
                radius={[4, 4, 0, 0]}
              />
            )}
            
            {/* 折线图：区间成交量趋势 */}
            {showLine && (
              <Line 
                type="monotone"
                dataKey="binVolume"
                name="区间成交量趋势"
                stroke="#f97316"
                strokeWidth={3}
                dot={false}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

