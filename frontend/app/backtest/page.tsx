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
type MainTab = 'create' | 'records' | 'detail'

const mainTabs: { key: MainTab; label: string }[] = [
  { key: 'create', label: '新建回测' },
  { key: 'records', label: '回测记录' },
  { key: 'detail', label: '回测详情' },
]

const detailTabs: { key: DetailTab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'trades', label: '成交明细' },
  { key: 'rounds', label: '回合交易' },
  { key: 'snapshots', label: '持仓快照' },
  { key: 'strategy', label: '策略配置' },
]

export default function BacktestPage() {
  const [mainTab, setMainTab] = useState<MainTab>('create')
  const [detailTab, setDetailTab] = useState<DetailTab>('overview')

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

  const [job, setJob] = useState<any>(null)
  const [jobs, setJobs] = useState<any[]>([])
  const [jobsLoading, setJobsLoading] = useState(false)
  const [result, setResult] = useState<any>(null)

  const [runs, setRuns] = useState<RunItem[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [runOverview, setRunOverview] = useState<any>(null)
  const [runTrades, setRunTrades] = useState<any[]>([])
  const [runRounds, setRunRounds] = useState<any[]>([])
  const [runSnapshots, setRunSnapshots] = useState<any[]>([])
  const [runStrategyConfig, setRunStrategyConfig] = useState<any>(null)
  const [detailLoading, setDetailLoading] = useState({
    overview: false,
    trades: false,
    rounds: false,
    snapshots: false,
    strategy: false,
  })

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_id === strategyId),
    [strategies, strategyId]
  )

  const fetchRuns = async () => {
    if (!isAuthenticated()) return
    setRunsLoading(true)
    try {
      const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs?limit=50&offset=0`, {
        headers: getAuthHeader(),
      })
      setRuns(resp.data?.data || [])
    } catch {
      // ignore
    } finally {
      setRunsLoading(false)
    }
  }

  const fetchJobs = async (showLoading = true) => {
    if (!isAuthenticated()) return
    if (showLoading) setJobsLoading(true)
    try {
      const resp = await axios.get(`${API_BASE_URL}/api/backtest/jobs`, {
        headers: getAuthHeader(),
      })
      setJobs(resp.data?.data || [])
    } catch {
      // ignore
    } finally {
      if (showLoading) setJobsLoading(false)
    }
  }

  const loadRunDetail = async (runId: number) => {
    const headers = getAuthHeader()

    setRunOverview(null)
    setRunTrades([])
    setRunRounds([])
    setRunSnapshots([])
    setRunStrategyConfig(null)
    setDetailLoading({
      overview: true,
      trades: true,
      rounds: true,
      snapshots: true,
      strategy: true,
    })

    setMainTab('detail')
    setDetailTab('overview')

    axios
      .get(`${API_BASE_URL}/api/backtest/runs/${runId}/overview`, { headers })
      .then((resp) => setRunOverview(resp.data?.data || null))
      .finally(() => setDetailLoading((prev) => ({ ...prev, overview: false })))

    axios
      .get(`${API_BASE_URL}/api/backtest/runs/${runId}/trades`, { headers })
      .then((resp) => setRunTrades(resp.data?.data || []))
      .finally(() => setDetailLoading((prev) => ({ ...prev, trades: false })))

    axios
      .get(`${API_BASE_URL}/api/backtest/runs/${runId}/rounds`, { headers })
      .then((resp) => setRunRounds(resp.data?.data || []))
      .finally(() => setDetailLoading((prev) => ({ ...prev, rounds: false })))

    axios
      .get(`${API_BASE_URL}/api/backtest/runs/${runId}/snapshots`, { headers })
      .then((resp) => setRunSnapshots(resp.data?.data || []))
      .finally(() => setDetailLoading((prev) => ({ ...prev, snapshots: false })))

    axios
      .get(`${API_BASE_URL}/api/backtest/runs/${runId}/strategy-config`, { headers })
      .then((resp) => setRunStrategyConfig(resp.data?.data || null))
      .finally(() => setDetailLoading((prev) => ({ ...prev, strategy: false })))
  }

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
    fetchJobs()
  }, [])

  useEffect(() => {
    if (!selectedStrategy) return
    const defaults: Record<string, string> = {}
    selectedStrategy.param_schema.forEach((p) => {
      defaults[p.key] = String(p.default ?? '')
    })
    setStrategyParams(defaults)
  }, [selectedStrategy])

  useEffect(() => {
    const timer = setInterval(() => fetchJobs(false), 3000)
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
      setMainTab('create')
      fetchJobs()
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
    <div className="max-w-7xl mx-auto py-8 px-4 space-y-5">
      <div className="rounded-2xl bg-gradient-to-r from-indigo-600 via-violet-600 to-purple-600 p-6 text-white shadow-lg">
        <h1 className="text-3xl font-bold">策略回测中心</h1>
        <p className="text-indigo-100 mt-1">三段式结构：新建任务 → 记录列表 → 详情钻取</p>
      </div>

      <div className="bg-white rounded-2xl shadow p-2 flex gap-2">
        {mainTabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setMainTab(tab.key)}
            className={`px-4 py-2 rounded-xl text-sm font-medium transition ${mainTab === tab.key ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-100'}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {mainTab === 'create' && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="xl:col-span-2 bg-white rounded-2xl shadow p-5 space-y-4">
            <div className="font-semibold text-gray-800">新建回测任务</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="text-sm">任务名称<input className="mt-1 w-full border rounded-lg px-3 py-2" value={name} onChange={(e) => setName(e.target.value)} /></label>
              <label className="text-sm">策略<select className="mt-1 w-full border rounded-lg px-3 py-2" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>{strategies.map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>)}</select></label>
              <label className="text-sm">回测模式<select className="mt-1 w-full border rounded-lg px-3 py-2" value={mode} onChange={(e) => setMode(e.target.value as 'manual_symbols' | 'strategy_select')}><option value="manual_symbols">手工股票池</option><option value="strategy_select">策略自动选股</option></select></label>
              {mode === 'manual_symbols' ? (
                <label className="text-sm">股票代码（逗号分隔）<input className="mt-1 w-full border rounded-lg px-3 py-2" value={symbols} onChange={(e) => setSymbols(e.target.value)} /></label>
              ) : (
                <label className="text-sm">选股范围<select className="mt-1 w-full border rounded-lg px-3 py-2" value={universeType} onChange={(e) => setUniverseType(e.target.value as 'all' | 'custom')}><option value="all">全市场</option><option value="custom">自定义池</option></select></label>
              )}
              {mode === 'strategy_select' && universeType === 'custom' && <label className="text-sm">自定义池<input className="mt-1 w-full border rounded-lg px-3 py-2" value={poolSymbols} onChange={(e) => setPoolSymbols(e.target.value)} /></label>}
              <label className="text-sm">初始资金<input className="mt-1 w-full border rounded-lg px-3 py-2" value={initialCash} onChange={(e) => setInitialCash(e.target.value)} /></label>
              <label className="text-sm">开始日期<input type="date" className="mt-1 w-full border rounded-lg px-3 py-2" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
              <label className="text-sm">结束日期<input type="date" className="mt-1 w-full border rounded-lg px-3 py-2" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
            </div>

            {selectedStrategy && (
              <div className="rounded-xl border bg-gray-50 p-4">
                <div className="font-medium mb-3">策略参数</div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {selectedStrategy.param_schema.map((p) => (
                    <label key={p.key} className="text-sm">{p.label}<input className="mt-1 w-full border rounded-lg px-3 py-2" value={strategyParams[p.key] ?? ''} onChange={(e) => setStrategyParams((prev) => ({ ...prev, [p.key]: e.target.value }))} /></label>
                  ))}
                </div>
              </div>
            )}

            <button onClick={handleRun} disabled={loading} className="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:bg-gray-400">{loading ? '提交中...' : '提交回测任务'}</button>
            {error && <div className="text-red-600 text-sm">{error}</div>}
          </div>

          <div className="bg-white rounded-2xl shadow p-5 space-y-3">
            <div className="font-semibold text-gray-800">任务状态</div>
            {job && <div className="text-sm rounded-lg bg-indigo-50 p-3">当前任务：{job.job_id}<br/>状态：{job.status} · 进度：{Number(job.progress_pct || 0).toFixed(1)}%</div>}
            <div className="text-sm text-gray-500">最近任务</div>
            <div className="max-h-96 overflow-auto space-y-2">
              {jobsLoading ? (
                <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">任务列表加载中...</div>
              ) : jobs.length > 0 ? (
                jobs.map((j) => (
                  <div key={j.job_id} className="border rounded-lg px-3 py-2 text-sm">
                    <div className="font-medium text-gray-700">{j.job_id}</div>
                    <div className="text-gray-500">{j.status} · {Number(j.progress_pct || 0).toFixed(1)}%</div>
                  </div>
                ))
              ) : (
                <div className="text-sm text-gray-400">暂无任务</div>
              )}
            </div>
          </div>
        </div>
      )}

      {mainTab === 'records' && (
        <div className="bg-white rounded-2xl shadow p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold text-gray-800">回测记录</div>
            <button className="text-sm text-indigo-600 hover:underline" onClick={fetchRuns}>刷新</button>
          </div>
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b text-gray-500">
                  <th className="py-2">ID</th><th>名称</th><th>策略</th><th>区间</th><th>总收益</th><th>最大回撤</th><th>状态</th><th>创建时间</th><th></th>
                </tr>
              </thead>
              <tbody>
                {runsLoading ? (
                  <tr><td className="py-3 text-gray-500" colSpan={9}>回测记录加载中...</td></tr>
                ) : runs.length > 0 ? (
                  runs.map((r) => (
                    <tr key={r.id} className="border-b hover:bg-gray-50">
                      <td className="py-2">#{r.id}</td>
                      <td>{r.name}</td>
                      <td>{r.strategy_id}</td>
                      <td>{r.start_date} ~ {r.end_date}</td>
                      <td>{r.summary?.total_return ?? '-'}</td>
                      <td>{r.summary?.max_drawdown ?? '-'}</td>
                      <td>{r.status}</td>
                      <td>{new Date(r.created_at).toLocaleString()}</td>
                      <td><button className="text-indigo-600 hover:underline" onClick={async () => { setSelectedRunId(r.id); setMainTab('detail'); await loadRunDetail(r.id) }}>查看详情</button></td>
                    </tr>
                  ))
                ) : (
                  <tr><td className="py-3 text-gray-500" colSpan={9}>暂无回测记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {mainTab === 'detail' && (
        <div className="bg-white rounded-2xl shadow p-5 space-y-4">
          <div className="font-semibold text-gray-800">回测详情 {selectedRunId ? `(Run #${selectedRunId})` : ''}</div>
          {!selectedRunId ? (
            <div className="text-sm text-gray-500">请先在「回测记录」中选择一条记录</div>
          ) : (
            <>
              <div className="flex gap-2 flex-wrap">
                {detailTabs.map((tab) => (
                  <button key={tab.key} onClick={() => setDetailTab(tab.key)} className={`px-3 py-1 rounded-lg text-sm ${detailTab === tab.key ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                    {tab.label}
                  </button>
                ))}
              </div>

              {detailTab === 'overview' && (
                <div className="space-y-4">
                  {detailLoading.overview ? (
                    <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">概览数据加载中...</div>
                  ) : (
                    <>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        {Object.entries(runOverview?.summary || {})
                          .filter(([k]) => !['positions_snapshot', 'final_positions'].includes(k))
                          .slice(0, 8)
                          .map(([k, v]) => (
                            <div key={k} className="border rounded-lg p-3 bg-gray-50"><div className="text-xs text-gray-500">{k}</div><div className="font-semibold">{String(v)}</div></div>
                          ))}
                      </div>
                      <div className="h-[360px] border rounded-lg p-2">
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={runOverview?.equity_curve || []} margin={{ top: 10, right: 20, left: 20, bottom: 10 }}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="datetime" tick={{ fontSize: 11 }} minTickGap={40} />
                            <YAxis tick={{ fontSize: 11 }} domain={['dataMin', 'dataMax']} />
                            <Tooltip />
                            <Line type="monotone" dataKey="equity" stroke="#4f46e5" strokeWidth={2} dot={false} />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    </>
                  )}
                </div>
              )}

              {detailTab === 'trades' && (
                <div className="space-y-2">
                  {detailLoading.trades ? (
                    <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">成交明细加载中...</div>
                  ) : (
                    <>
                      <button className="inline-block px-3 py-1 text-xs rounded bg-indigo-50 text-indigo-700" onClick={() => downloadCsv(`${API_BASE_URL}/api/backtest/runs/${selectedRunId}/trades/export`, `backtest_run_${selectedRunId}_trades.csv`)}>导出成交 CSV</button>
                      <div className="overflow-auto max-h-[480px]">
                        <table className="w-full text-sm"><thead><tr className="border-b"><th className="py-2">时间</th><th>标的</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th><th>手续费</th><th>原因</th></tr></thead><tbody>{runTrades.map((t, i) => <tr key={i} className="border-b"><td className="py-1">{t.datetime}</td><td>{t.symbol}</td><td>{t.side}</td><td>{t.price}</td><td>{t.qty}</td><td>{t.amount}</td><td>{t.fee}</td><td>{t.reason || '-'}</td></tr>)}{runTrades.length === 0 && <tr><td colSpan={8} className="py-2 text-gray-500">暂无成交数据</td></tr>}</tbody></table>
                      </div>
                    </>
                  )}
                </div>
              )}

              {detailTab === 'rounds' && (
                <div className="space-y-2">
                  {detailLoading.rounds ? (
                    <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">回合交易加载中...</div>
                  ) : (
                    <>
                      <button className="inline-block px-3 py-1 text-xs rounded bg-indigo-50 text-indigo-700" onClick={() => downloadCsv(`${API_BASE_URL}/api/backtest/runs/${selectedRunId}/rounds/export`, `backtest_run_${selectedRunId}_rounds.csv`)}>导出回合 CSV</button>
                      <div className="overflow-auto max-h-[480px]">
                        <table className="w-full text-sm"><thead><tr className="border-b"><th className="py-2">标的</th><th>开仓</th><th>平仓</th><th>持有天数</th><th>收益率</th><th>盈亏</th><th>退出原因</th></tr></thead><tbody>{runRounds.map((r, i) => <tr key={i} className="border-b"><td className="py-1">{r.symbol}</td><td>{r.open_time} @ {r.open_price}</td><td>{r.close_time} @ {r.close_price}</td><td>{r.holding_days ?? '-'}</td><td>{r.pnl_ratio}</td><td>{r.pnl_amount}</td><td>{r.exit_reason || '-'}</td></tr>)}{runRounds.length === 0 && <tr><td colSpan={7} className="py-2 text-gray-500">暂无回合交易数据</td></tr>}</tbody></table>
                      </div>
                    </>
                  )}
                </div>
              )}

              {detailTab === 'snapshots' && (
                <div className="space-y-3 text-sm">
                  {detailLoading.snapshots ? (
                    <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">持仓快照加载中...</div>
                  ) : runSnapshots.length === 0 ? (
                    <div className="text-gray-500">暂无持仓快照数据</div>
                  ) : (
                    runSnapshots.slice(-20).reverse().map((s, i) => (
                      <div key={i} className="border rounded-lg p-3 bg-gray-50">
                        <div className="font-medium">{s.snapshot_time}</div>
                        <div className="text-xs text-gray-600 mt-1">权益: {s.equity} | 现金: {s.cash} | 持仓市值: {s.position_value}</div>
                        <div className="overflow-auto mt-2"><table className="w-full text-xs"><thead><tr className="border-b"><th className="text-left py-1">标的</th><th>数量</th><th>现价</th><th>市值</th><th>权重</th></tr></thead><tbody>{(s.holdings || []).map((h: any, hi: number) => <tr key={hi} className="border-b"><td className="py-1">{h.symbol}</td><td>{h.qty}</td><td>{h.last_price}</td><td>{h.market_value}</td><td>{h.weight}</td></tr>)}{(s.holdings || []).length === 0 && <tr><td colSpan={5} className="py-1 text-gray-500">空仓</td></tr>}</tbody></table></div>
                      </div>
                    ))
                  )}
                </div>
              )}

              {detailTab === 'strategy' && (
                detailLoading.strategy ? (
                  <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">策略配置加载中...</div>
                ) : (
                  <pre className="bg-gray-50 border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(runStrategyConfig || {}, null, 2)}</pre>
                )
              )}
            </>
          )}
        </div>
      )}

      {result && (
        <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-xl px-4 py-3 text-sm">
          最新任务完成：Run #{result.run_id}，交易笔数 {result.trades?.length || 0}。
          <button className="ml-3 text-emerald-800 underline" onClick={() => mainTab !== 'records' && setMainTab('records')}>去记录页查看</button>
        </div>
      )}
    </div>
  )
}
