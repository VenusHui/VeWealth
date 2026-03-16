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
  }>
}

type RunItem = {
  id: number
  name: string
  status: string
  strategy_id: string
  start_date: string
  end_date: string
  created_at: string
  summary?: Record<string, any>
}

type DetailTab = 'overview' | 'trades' | 'rounds' | 'snapshots' | 'strategy'

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

  const [runs, setRuns] = useState<RunItem[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [detailTab, setDetailTab] = useState<DetailTab>('overview')
  const [runOverview, setRunOverview] = useState<any>(null)
  const [runTrades, setRunTrades] = useState<any[]>([])
  const [runRounds, setRunRounds] = useState<any[]>([])
  const [runSnapshots, setRunSnapshots] = useState<any[]>([])
  const [runStrategyConfig, setRunStrategyConfig] = useState<any>(null)

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_id === strategyId),
    [strategies, strategyId]
  )

  const fetchRuns = async () => {
    if (!isAuthenticated()) return
    try {
      const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs?limit=20&offset=0`, {
        headers: getAuthHeader(),
      })
      setRuns(resp.data?.data || [])
    } catch {
      // ignore
    }
  }

  const loadRunDetail = async (runId: number) => {
    const headers = getAuthHeader()
    const [overviewResp, tradesResp, roundsResp, snapshotsResp, strategyResp] = await Promise.all([
      axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/overview`, { headers }),
      axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/trades`, { headers }),
      axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/rounds`, { headers }),
      axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/snapshots`, { headers }),
      axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/strategy-config`, { headers }),
    ])

    setRunOverview(overviewResp.data?.data || null)
    setRunTrades(tradesResp.data?.data || [])
    setRunRounds(roundsResp.data?.data || [])
    setRunSnapshots(snapshotsResp.data?.data || [])
    setRunStrategyConfig(strategyResp.data?.data || null)
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
    fetchRuns()
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
          fetchRuns()
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

  const downloadCsv = async (url: string, filename: string) => {
    const resp = await axios.get(url, {
      headers: getAuthHeader(),
      responseType: 'blob',
    })
    const blobUrl = window.URL.createObjectURL(new Blob([resp.data], { type: 'text/csv;charset=utf-8;' }))
    const link = document.createElement('a')
    link.href = blobUrl
    link.setAttribute('download', filename)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

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
      setJob(resp.data?.data)
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
    <div className="max-w-6xl mx-auto py-8 px-4 space-y-6">
      <h1 className="text-3xl font-bold text-gray-800">📈 策略回测</h1>

      <div className="bg-white p-6 rounded-lg shadow space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="text-sm text-gray-700">回测名称<input className="mt-1 w-full border rounded px-3 py-2" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="text-sm text-gray-700">策略
            <select className="mt-1 w-full border rounded px-3 py-2" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              {strategies.map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>)}
            </select>
          </label>
          <label className="text-sm text-gray-700">回测模式
            <select className="mt-1 w-full border rounded px-3 py-2" value={mode} onChange={(e) => setMode(e.target.value as 'manual_symbols' | 'strategy_select')}>
              <option value="manual_symbols">手工股票池</option>
              <option value="strategy_select">策略自动选股</option>
            </select>
          </label>
          {mode === 'manual_symbols' ? (
            <label className="text-sm text-gray-700">股票代码（逗号分隔）<input className="mt-1 w-full border rounded px-3 py-2" value={symbols} onChange={(e) => setSymbols(e.target.value)} /></label>
          ) : (
            <>
              <label className="text-sm text-gray-700">选股范围
                <select className="mt-1 w-full border rounded px-3 py-2" value={universeType} onChange={(e) => setUniverseType(e.target.value as 'all' | 'custom')}>
                  <option value="all">全市场</option>
                  <option value="custom">自定义池</option>
                </select>
              </label>
              {universeType === 'custom' && (
                <label className="text-sm text-gray-700">自定义股票池<input className="mt-1 w-full border rounded px-3 py-2" value={poolSymbols} onChange={(e) => setPoolSymbols(e.target.value)} /></label>
              )}
            </>
          )}
          <label className="text-sm text-gray-700">初始资金<input className="mt-1 w-full border rounded px-3 py-2" value={initialCash} onChange={(e) => setInitialCash(e.target.value)} /></label>
          <label className="text-sm text-gray-700">开始日期<input type="date" className="mt-1 w-full border rounded px-3 py-2" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
          <label className="text-sm text-gray-700">结束日期<input type="date" className="mt-1 w-full border rounded px-3 py-2" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
        </div>

        {selectedStrategy && (
          <div className="border rounded p-3 bg-gray-50">
            <div className="font-medium mb-2">策略参数</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {selectedStrategy.param_schema.map((p) => (
                <label key={p.key} className="text-sm text-gray-700">
                  {p.label}
                  <input className="mt-1 w-full border rounded px-3 py-2" value={strategyParams[p.key] ?? ''} onChange={(e) => setStrategyParams((prev) => ({ ...prev, [p.key]: e.target.value }))} />
                </label>
              ))}
            </div>
          </div>
        )}

        <button onClick={handleRun} disabled={loading} className="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700 disabled:bg-gray-400">{loading ? '执行中...' : '开始回测'}</button>
        {error && <div className="text-red-600">{error}</div>}
      </div>

      <div className="bg-white p-4 rounded-lg shadow">
        <div className="font-semibold mb-3">回测记录（任务级摘要）</div>
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b">
                <th className="py-2">ID</th><th>名称</th><th>策略</th><th>区间</th><th>总收益</th><th>最大回撤</th><th>状态</th><th>创建时间</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b hover:bg-gray-50 cursor-pointer" onClick={async () => { setSelectedRunId(r.id); setDetailTab('overview'); await loadRunDetail(r.id) }}>
                  <td className="py-2">#{r.id}</td>
                  <td>{r.name}</td>
                  <td>{r.strategy_id}</td>
                  <td>{r.start_date} ~ {r.end_date}</td>
                  <td>{r.summary?.total_return ?? '-'}</td>
                  <td>{r.summary?.max_drawdown ?? '-'}</td>
                  <td>{r.status}</td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
              {runs.length === 0 && <tr><td className="py-3 text-gray-500" colSpan={8}>暂无回测记录</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {selectedRunId && runOverview && (
        <div className="bg-white p-4 rounded-lg shadow space-y-4">
          <div className="font-semibold">回测详情 - Run #{selectedRunId}</div>
          <div className="flex gap-2 flex-wrap">
            {[
              ['overview', '概览'],
              ['trades', '成交明细'],
              ['rounds', '回合交易'],
              ['snapshots', '持仓快照'],
              ['strategy', '策略参数'],
            ].map(([key, label]) => (
              <button key={key} onClick={() => setDetailTab(key as DetailTab)} className={`px-3 py-1 rounded text-sm ${detailTab === key ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700'}`}>
                {label}
              </button>
            ))}
          </div>

          {detailTab === 'overview' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {Object.entries(runOverview.summary || {}).slice(0, 8).map(([k, v]) => (
                  <div key={k} className="border rounded p-2 bg-gray-50 text-sm"><div className="text-gray-500">{k}</div><div className="font-semibold">{String(v)}</div></div>
                ))}
              </div>
              <div className="h-[320px] border rounded p-2">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={runOverview.equity_curve || []} margin={{ top: 10, right: 20, left: 20, bottom: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="datetime" tick={{ fontSize: 11 }} minTickGap={40} />
                    <YAxis tick={{ fontSize: 11 }} domain={['dataMin', 'dataMax']} />
                    <Tooltip />
                    <Line type="monotone" dataKey="equity" stroke="#4f46e5" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {detailTab === 'trades' && (
            <div className="space-y-2">
              <div>
                <button
                  className="inline-block px-3 py-1 text-xs rounded bg-indigo-50 text-indigo-700"
                  onClick={() => downloadCsv(`${API_BASE_URL}/api/backtest/runs/${selectedRunId}/trades/export`, `backtest_run_${selectedRunId}_trades.csv`)}
                >
                  导出成交 CSV
                </button>
              </div>
              <div className="overflow-auto max-h-96">
                <table className="w-full text-sm">
                  <thead><tr className="border-b"><th className="py-2">时间</th><th>标的</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>手续费</th><th>原因</th></tr></thead>
                  <tbody>
                    {runTrades.map((t, i) => <tr key={i} className="border-b"><td className="py-1">{t.datetime}</td><td>{t.symbol}</td><td>{t.side}</td><td>{t.price}</td><td>{t.qty}</td><td>{t.amount}</td><td>{t.fee}</td><td>{t.reason || '-'}</td></tr>)}
                    {runTrades.length === 0 && <tr><td colSpan={8} className="py-2 text-gray-500">暂无成交数据</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {detailTab === 'rounds' && (
            <div className="space-y-2">
              <div>
                <button
                  className="inline-block px-3 py-1 text-xs rounded bg-indigo-50 text-indigo-700"
                  onClick={() => downloadCsv(`${API_BASE_URL}/api/backtest/runs/${selectedRunId}/rounds/export`, `backtest_run_${selectedRunId}_rounds.csv`)}
                >
                  导出回合 CSV
                </button>
              </div>
              <div className="overflow-auto max-h-96">
                <table className="w-full text-sm">
                  <thead><tr className="border-b"><th className="py-2">标的</th><th>开仓</th><th>平仓</th><th>持有天数</th><th>收益率</th><th>盈亏</th><th>退出原因</th></tr></thead>
                  <tbody>
                    {runRounds.map((r, i) => <tr key={i} className="border-b"><td className="py-1">{r.symbol}</td><td>{r.open_time} @ {r.open_price}</td><td>{r.close_time} @ {r.close_price}</td><td>{r.holding_days ?? '-'}</td><td>{r.pnl_ratio}</td><td>{r.pnl_amount}</td><td>{r.exit_reason || '-'}</td></tr>)}
                    {runRounds.length === 0 && <tr><td colSpan={7} className="py-2 text-gray-500">暂无回合交易数据</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {detailTab === 'snapshots' && (
            <div className="space-y-3 text-sm">
              {runSnapshots.length === 0 ? (
                <div className="text-gray-500">暂无持仓快照数据</div>
              ) : (
                runSnapshots.slice(-20).reverse().map((s, i) => (
                  <div key={i} className="border rounded p-3 bg-gray-50">
                    <div className="font-medium">{s.snapshot_time}</div>
                    <div className="text-xs text-gray-600 mt-1">权益: {s.equity} | 现金: {s.cash} | 持仓市值: {s.position_value}</div>
                    <div className="overflow-auto mt-2">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b"><th className="text-left py-1">标的</th><th className="text-left">数量</th><th className="text-left">现价</th><th className="text-left">市值</th><th className="text-left">权重</th></tr>
                        </thead>
                        <tbody>
                          {(s.holdings || []).map((h: any, hi: number) => (
                            <tr key={hi} className="border-b"><td className="py-1">{h.symbol}</td><td>{h.qty}</td><td>{h.last_price}</td><td>{h.market_value}</td><td>{h.weight}</td></tr>
                          ))}
                          {(s.holdings || []).length === 0 && <tr><td colSpan={5} className="py-1 text-gray-500">空仓</td></tr>}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {detailTab === 'strategy' && (
            <pre className="bg-gray-50 border rounded p-3 text-xs overflow-auto">{JSON.stringify(runStrategyConfig || {}, null, 2)}</pre>
          )}
        </div>
      )}

      {result && (
        <div className="bg-white p-6 rounded-lg shadow space-y-3">
          <h2 className="text-xl font-semibold">最新任务结果预览（Run #{result.run_id}）</h2>
          <div className="text-sm text-gray-600">交易笔数：{result.trades?.length || 0}，净值点数：{result.equity_curve?.length || 0}</div>
        </div>
      )}
    </div>
  )
}
