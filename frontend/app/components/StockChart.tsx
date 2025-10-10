'use client'

import { useMemo } from 'react'
import { 
  LineChart,
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
  priceRange: string
  price: number
  totalVolume: number
  count: number
  percentage: number
}

export default function StockChart({ data, period }: StockChartProps) {
  // 聚合相同价格区间的成交量
  const aggregatedData = useMemo(() => {
    if (!data || data.length === 0) return []

    // 计算价格范围
    const prices = data.map(d => d.price)
    const minPrice = Math.min(...prices)
    const maxPrice = Math.max(...prices)
    const priceRange = maxPrice - minPrice

    // 动态确定价格区间大小（分成约50个区间）
    const numBins = Math.min(50, data.length)
    const binSize = priceRange / numBins || 0.01

    // 按价格区间分组并累加成交量
    const priceMap = new Map<number, { volume: number; count: number }>()
    
    data.forEach(point => {
      // 将价格归入最接近的区间
      const binPrice = Math.floor(point.price / binSize) * binSize
      const existing = priceMap.get(binPrice) || { volume: 0, count: 0 }
      priceMap.set(binPrice, {
        volume: existing.volume + point.volume,
        count: existing.count + 1
      })
    })

    // 转换为数组并排序
    const result = Array.from(priceMap.entries())
      .map(([price, data]) => ({
        price: Number(price.toFixed(2)),
        priceRange: `${price.toFixed(2)}-${(price + binSize).toFixed(2)}`,
        totalVolume: data.volume,
        count: data.count,
        percentage: 0 // 稍后计算
      }))
      .sort((a, b) => a.price - b.price)

    // 计算百分比
    const totalVolume = result.reduce((sum, item) => sum + item.totalVolume, 0)
    result.forEach(item => {
      item.percentage = (item.totalVolume / totalVolume) * 100
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
            价格区间: ¥{data.priceRange}
          </p>
          <div className="space-y-1">
            <p className="text-sm text-blue-600">
              累计成交量: {data.totalVolume >= 100000000 
                ? `${(data.totalVolume / 100000000).toFixed(2)}亿` 
                : data.totalVolume >= 10000 
                  ? `${(data.totalVolume / 10000).toFixed(0)}万`
                  : data.totalVolume.toLocaleString()}
            </p>
            <p className="text-sm text-green-600">
              占比: {data.percentage.toFixed(2)}%
            </p>
            <p className="text-sm text-gray-600">
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
        <h3 className="text-lg font-semibold text-gray-700 mb-2">
          价格-成交量分布图
        </h3>
        <ResponsiveContainer width="100%" height="90%">
          <LineChart
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
              dataKey="totalVolume" 
              name="累计成交量"
              label={{ 
                value: '累计成交量', 
                angle: -90, 
                position: 'insideLeft',
                style: { fontSize: '14px', fontWeight: 'bold' }
              }}
              tick={{ fontSize: 12 }}
              tickFormatter={formatVolume}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3' }} />
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
            
            <Line 
              type="monotone"
              dataKey="totalVolume"
              name="成交量分布"
              stroke="#3b82f6"
              strokeWidth={3}
              dot={{ r: 3, fill: '#3b82f6' }}
              activeDot={{ r: 6 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

