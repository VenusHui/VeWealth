'use client'

import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
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
  const [mode, setMode] = useState<'manual_symbols' | 'strategy_select'>('manual_symbols')
  const [universeType, setUniverseType] = useState<'all' | 'custom'>('all')
  const [symbols, setSymbols] = useState('000001')
  const [poolSymbols, setPoolSymbols] = useState('')
  const [startDate, setStartDate] = useState('2025-01-01')
  const [endDate, setEndDate] = useState('2025-12-31')
  const [initialCash, setInitialCash] = useState('100000')
  const [name, setName] = useState('我的回测')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<any>(null)
  const [job, setJob] = useState<any>(null)
  const [jobs, setJobs] = useState<any[]>([])

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_id === strategyId),
    [strategies, strategyId]
  )

  const metricDescriptions: Record<string, string> = {
    total_return: '总收益率：期末资金相对初始资金的涨跌幅。',
    annual_return: '年化收益率：将区间收益折算到全年水平。',
    max_drawdown: '最大回撤：资金从阶段高点回落的最大幅度。',
    sharpe: '夏普比率：单位波动风险对应的超额收益能力。',
    win_rate: '胜率：盈利平仓交易占全部平仓交易的比例。',
    profit_loss_ratio: '盈亏比：平均盈利金额 / 平均亏损金额。',
    turnover: '换手率：累计成交金额 / 初始资金。',
    total_trades: '总交易笔数：回测区间内买卖成交总笔数。',
  }

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

  const fetchJobs = async () => {
    if (!isAuthenticated()) return
    try {
      const resp = await axios.get(`${API_BASE_URL}/api/backtest/jobs`, {
        headers: getAuthHeader(),
      })
      setJobs(resp.data?.data || [])
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    fetchJobs()
    const timer = setInterval(fetchJobs, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!job?.job_id) return
    const timer = setInterval(async () => {
      try {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/jobs/${job.job_id}`, {
          headers: getAuthHeader(),
        })
        const detail = resp.data?.data
        setJob(detail)
        if (detail?.status === 'success') {
          setResult(detail.result)
          clearInterval(timer)
          fetchJobs()
        }
        if (detail?.status === 'failed' || detail?.status === 'cancelled') {
          clearInterval(timer)
        }
      } catch {
        clearInterval(timer)
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [job?.job_id])

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
        mode,
        universe_type: universeType,
        symbols: mode === 'manual_symbols' ? symbols.split(',').map((s) => s.trim()).filter(Boolean) : [],
        pool_symbols: mode === 'strategy_select' && universeType === 'custom'
          ? poolSymbols.split(',').map((s) => s.trim()).filter(Boolean)
          : [],
        start_date: startDate,
        end_date: endDate,
        initial_cash: Number(initialCash),
      }

      const resp = await axios.post(`${API_BASE_URL}/api/backtest/jobs`, payload, {
        headers: getAuthHeader(),
      })
      const createdJob = resp.data?.data
      setJob(createdJob)
      setResult(null)
      await fetchJobs()
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
          <label className="text-sm text-gray-700">
            回测名称
            <span className="ml-2 text-xs text-gray-500">(name)</span>
            <input className="mt-1 w-full border rounded px-3 py-2" value={name} onChange={(e) => setName(e.target.value)} placeholder="回测名称" />
          </label>

          <label className="text-sm text-gray-700">
            策略
            <span className="ml-2 text-xs text-gray-500">(strategy_id)</span>
            <select className="mt-1 w-full border rounded px-3 py-2" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              {strategies.map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>)}
            </select>
          </label>

          <label className="text-sm text-gray-700">
            回测模式
            <span className="ml-2 text-xs text-gray-500">(mode)</span>
            <select className="mt-1 w-full border rounded px-3 py-2" value={mode} onChange={(e) => setMode(e.target.value as 'manual_symbols' | 'strategy_select')}>
              <option value="manual_symbols">手工股票池 (manual_symbols)</option>
              <option value="strategy_select">策略自动选股 (strategy_select)</option>
            </select>
          </label>

          {mode === 'manual_symbols' ? (
            <label className="text-sm text-gray-700">
              股票代码（逗号分隔）
              <span className="ml-2 text-xs text-gray-500">(symbols)</span>
              <input className="mt-1 w-full border rounded px-3 py-2" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="股票代码，逗号分隔" />
            </label>
          ) : (
            <>
              <label className="text-sm text-gray-700">
                选股范围
                <span className="ml-2 text-xs text-gray-500">(universe_type)</span>
                <select className="mt-1 w-full border rounded px-3 py-2" value={universeType} onChange={(e) => setUniverseType(e.target.value as 'all' | 'custom')}>
                  <option value="all">全市场 (all)</option>
                  <option value="custom">自定义池 (custom)</option>
                </select>
              </label>

              {universeType === 'custom' && (
                <label className="text-sm text-gray-700">
                  自定义股票池（逗号分隔）
                  <span className="ml-2 text-xs text-gray-500">(pool_symbols)</span>
                  <input className="mt-1 w-full border rounded px-3 py-2" value={poolSymbols} onChange={(e) => setPoolSymbols(e.target.value)} placeholder="000001,000002" />
                </label>
              )}
            </>
          )}

          <label className="text-sm text-gray-700">
            初始资金
            <span className="ml-2 text-xs text-gray-500">(initial_cash)</span>
            <input className="mt-1 w-full border rounded px-3 py-2" value={initialCash} onChange={(e) => setInitialCash(e.target.value)} placeholder="初始资金" />
          </label>

          <label className="text-sm text-gray-700">
            开始日期
            <span className="ml-2 text-xs text-gray-500">(start_date)</span>
            <input type="date" className="mt-1 w-full border rounded px-3 py-2" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>

          <label className="text-sm text-gray-700">
            结束日期
            <span className="ml-2 text-xs text-gray-500">(end_date)</span>
            <input type="date" className="mt-1 w-full border rounded px-3 py-2" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
        </div>

        {selectedStrategy && (
          <div className="border rounded p-3 bg-gray-50">
            <div className="font-medium mb-2">策略参数</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {selectedStrategy.param_schema.map((p) => (
                <label key={p.key} className="text-sm text-gray-700">
                  {p.label}
                  <span className="ml-2 text-xs text-gray-500">(strategy_params.{p.key})</span>
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

      {job && (
        <div className="bg-white p-4 rounded-lg shadow space-y-2">
          <div className="font-medium">当前任务：{job.job_id}</div>
          <div className="text-sm text-gray-600">状态：{job.status}｜阶段：{job.stage}</div>
          <div className="text-sm text-gray-600">进度：{job.processed_symbols}/{job.total_symbols} ({Number(job.progress_pct || 0).toFixed(1)}%)</div>
          {job.error && <div className="text-sm text-red-600">错误：{job.error}</div>}
        </div>
      )}

      <div className="bg-white p-4 rounded-lg shadow">
        <div className="font-medium mb-2">离线任务列表</div>
        <div className="space-y-2 max-h-64 overflow-auto">
          {jobs.map((j) => (
            <div key={j.job_id} className="text-sm border rounded px-3 py-2 flex justify-between items-center">
              <span>{j.job_id}</span>
              <span className="text-gray-600">{j.status} · {Number(j.progress_pct || 0).toFixed(1)}%</span>
            </div>
          ))}
          {jobs.length === 0 && <div className="text-sm text-gray-500">暂无任务</div>}
        </div>
      </div>

      {result && (
        <div className="bg-white p-6 rounded-lg shadow space-y-6">
          <h2 className="text-xl font-semibold">回测结果（Run #{result.run_id}）</h2>

          <div>
            <div className="font-medium mb-3">指标说明</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {Object.entries(result.summary || {}).map(([key, value]) => (
                <div key={key} className="border rounded p-3 bg-gray-50">
                  <div className="text-sm font-semibold text-gray-800">{key}: <span className="text-indigo-600">{String(value)}</span></div>
                  <div className="text-xs text-gray-600 mt-1">{metricDescriptions[key] || '—'}</div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="font-medium mb-2">整体资金曲线</div>
            <div className="h-[320px] border rounded p-2">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={result.equity_curve || []} margin={{ top: 10, right: 20, left: 20, bottom: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="datetime" tick={{ fontSize: 11 }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 11 }} domain={["dataMin", "dataMax"]} />
                  <Tooltip />
                  <Line type="monotone" dataKey="equity" stroke="#4f46e5" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="text-sm text-gray-600">交易笔数：{result.trades?.length || 0}，净值点数：{result.equity_curve?.length || 0}</div>
        </div>
      )}
    </div>
  )
}
