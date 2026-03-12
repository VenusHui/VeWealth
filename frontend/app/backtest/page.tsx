'use client'

import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { getAuthHeader, isAuthenticated } from '../lib/auth'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

type Strategy = {
  strategy_id: string
  name: string
  description: string
  param_schema: Array<{
    key: string
    label: string
    type: string
    default?: number | string
    min?: number
    max?: number
  }>
}

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [strategyParams, setStrategyParams] = useState<Record<string, string>>({})
  const [symbols, setSymbols] = useState('000001')
  const [startDate, setStartDate] = useState('2025-01-01')
  const [endDate, setEndDate] = useState('2025-12-31')
  const [initialCash, setInitialCash] = useState('100000')
  const [name, setName] = useState('我的回测')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<any>(null)

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_id === strategyId),
    [strategies, strategyId]
  )

  useEffect(() => {
    if (!isAuthenticated()) return

    const fetchStrategies = async () => {
      try {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/strategies`, {
          headers: getAuthHeader(),
        })
        const list = resp.data?.data || []
        setStrategies(list)
        if (list.length > 0) {
          setStrategyId(list[0].strategy_id)
          const defaults: Record<string, string> = {}
          list[0].param_schema.forEach((p: any) => {
            defaults[p.key] = String(p.default ?? '')
          })
          setStrategyParams(defaults)
        }
      } catch (e: any) {
        setError(e.response?.data?.detail || '加载策略列表失败')
      }
    }

    fetchStrategies()
  }, [])

  useEffect(() => {
    if (!selectedStrategy) return
    const defaults: Record<string, string> = {}
    selectedStrategy.param_schema.forEach((p) => {
      defaults[p.key] = String(p.default ?? '')
    })
    setStrategyParams(defaults)
  }, [selectedStrategy])

  const handleRun = async () => {
    if (!isAuthenticated()) {
      setError('请先登录')
      return
    }

    try {
      setLoading(true)
      setError('')
      setResult(null)

      const castParams: Record<string, any> = {}
      Object.keys(strategyParams).forEach((k) => {
        const val = strategyParams[k]
        castParams[k] = /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val
      })

      const payload = {
        name,
        strategy_id: strategyId,
        strategy_params: castParams,
        symbols: symbols.split(',').map((s) => s.trim()).filter(Boolean),
        start_date: startDate,
        end_date: endDate,
        initial_cash: Number(initialCash),
      }

      const resp = await axios.post(`${API_BASE_URL}/api/backtest/run`, payload, {
        headers: getAuthHeader(),
      })
      setResult(resp.data?.data)
    } catch (e: any) {
      setError(e.response?.data?.detail || '回测执行失败')
    } finally {
      setLoading(false)
    }
  }

  if (!isAuthenticated()) {
    return <div className="max-w-3xl mx-auto py-10 px-4">请先登录后使用回测功能。</div>
  }

  return (
    <div className="max-w-5xl mx-auto py-8 px-4 space-y-6">
      <h1 className="text-3xl font-bold text-gray-800">📈 策略回测</h1>

      <div className="bg-white p-6 rounded-lg shadow space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <input className="border rounded px-3 py-2" value={name} onChange={(e) => setName(e.target.value)} placeholder="回测名称" />
          <select className="border rounded px-3 py-2" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
            {strategies.map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>)}
          </select>
          <input className="border rounded px-3 py-2" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="股票代码，逗号分隔" />
          <input className="border rounded px-3 py-2" value={initialCash} onChange={(e) => setInitialCash(e.target.value)} placeholder="初始资金" />
          <input type="date" className="border rounded px-3 py-2" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          <input type="date" className="border rounded px-3 py-2" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </div>

        {selectedStrategy && (
          <div className="border rounded p-3 bg-gray-50">
            <div className="font-medium mb-2">策略参数</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {selectedStrategy.param_schema.map((p) => (
                <label key={p.key} className="text-sm text-gray-700">
                  {p.label}
                  <input
                    className="mt-1 w-full border rounded px-3 py-2"
                    value={strategyParams[p.key] ?? ''}
                    onChange={(e) => setStrategyParams((prev) => ({ ...prev, [p.key]: e.target.value }))}
                  />
                </label>
              ))}
            </div>
          </div>
        )}

        <button onClick={handleRun} disabled={loading} className="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700 disabled:bg-gray-400">
          {loading ? '执行中...' : '开始回测'}
        </button>

        {error && <div className="text-red-600">{error}</div>}
      </div>

      {result && (
        <div className="bg-white p-6 rounded-lg shadow space-y-4">
          <h2 className="text-xl font-semibold">回测结果（Run #{result.run_id}）</h2>
          <pre className="text-xs bg-gray-100 p-3 rounded overflow-auto">{JSON.stringify(result.summary, null, 2)}</pre>
          <div className="text-sm text-gray-600">交易笔数：{result.trades?.length || 0}，净值点数：{result.equity_curve?.length || 0}</div>
        </div>
      )}
    </div>
  )
}
