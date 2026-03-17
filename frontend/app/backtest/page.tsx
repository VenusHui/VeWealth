'use client'

import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { getAuthHeader, isAuthenticated } from '../lib/auth'
import { MainTabSwitcher } from './components/MainTabSwitcher'
import { BacktestRecordsPanel } from './components/BacktestRecordsPanel'
import { BacktestDetailPanel } from './components/BacktestDetailPanel'

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

const ACTIVE_JOB_STATUSES = ['pending', 'running'] as const

const LoadingHint = ({ text }: { text: string }) => (
  <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">{text}</div>
)

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
  const [detailLoaded, setDetailLoaded] = useState({
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
      const resp = await axios.get(`${API_BASE_URL}/api/backtest/jobs?limit=50&offset=0`, {
        headers: getAuthHeader(),
      })
      setJobs(resp.data?.data || [])
    } catch {
      // ignore
    } finally {
      if (showLoading) setJobsLoading(false)
    }
  }

  const loadRunDetail = (runId: number) => {
    setRunOverview(null)
    setRunTrades([])
    setRunRounds([])
    setRunSnapshots([])
    setRunStrategyConfig(null)
    setDetailLoading({
      overview: false,
      trades: false,
      rounds: false,
      snapshots: false,
      strategy: false,
    })
    setDetailLoaded({
      overview: false,
      trades: false,
      rounds: false,
      snapshots: false,
      strategy: false,
    })

    setSelectedRunId(runId)
    setMainTab('detail')
    setDetailTab('overview')
  }

  const loadDetailTabData = async (runId: number, tab: DetailTab) => {
    const headers = getAuthHeader()
    setDetailLoading((prev) => ({ ...prev, [tab]: true }))

    try {
      if (tab === 'overview') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/overview`, { headers })
        setRunOverview(resp.data?.data || null)
      }
      if (tab === 'trades') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/trades`, { headers })
        setRunTrades(resp.data?.data || [])
      }
      if (tab === 'rounds') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/rounds`, { headers })
        setRunRounds(resp.data?.data || [])
      }
      if (tab === 'snapshots') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/snapshots`, { headers })
        setRunSnapshots(resp.data?.data || [])
      }
      if (tab === 'strategy') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/strategy-config`, { headers })
        setRunStrategyConfig(resp.data?.data || null)
      }
    } finally {
      setDetailLoading((prev) => ({ ...prev, [tab]: false }))
      setDetailLoaded((prev) => ({ ...prev, [tab]: true }))
    }
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
    const hasActive = (job && ACTIVE_JOB_STATUSES.includes(job.status as (typeof ACTIVE_JOB_STATUSES)[number]))
      || jobs.some((j) => ACTIVE_JOB_STATUSES.includes(j.status as (typeof ACTIVE_JOB_STATUSES)[number]))

    if (mainTab !== 'create' || !hasActive) return

    const timer = setInterval(() => fetchJobs(false), 10000)
    return () => clearInterval(timer)
  }, [mainTab, jobs, job])

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
    }, 10000)
    return () => clearInterval(timer)
  }, [job?.job_id])

  useEffect(() => {
    if (mainTab !== 'detail' || !selectedRunId) return

    if (detailTab === 'overview' && !detailLoaded.overview && !detailLoading.overview) {
      loadDetailTabData(selectedRunId, 'overview')
      return
    }
    if (detailTab === 'trades' && !detailLoaded.trades && !detailLoading.trades) {
      loadDetailTabData(selectedRunId, 'trades')
      return
    }
    if (detailTab === 'rounds' && !detailLoaded.rounds && !detailLoading.rounds) {
      loadDetailTabData(selectedRunId, 'rounds')
      return
    }
    if (detailTab === 'snapshots' && !detailLoaded.snapshots && !detailLoading.snapshots) {
      loadDetailTabData(selectedRunId, 'snapshots')
      return
    }
    if (detailTab === 'strategy' && !detailLoaded.strategy && !detailLoading.strategy) {
      loadDetailTabData(selectedRunId, 'strategy')
    }
  }, [mainTab, selectedRunId, detailTab, detailLoaded, detailLoading])

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

      <MainTabSwitcher activeTab={mainTab} onChange={setMainTab} />

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
                <LoadingHint text="任务列表加载中..." />
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
        <BacktestRecordsPanel
          runs={runs}
          runsLoading={runsLoading}
          onRefresh={fetchRuns}
          onViewDetail={loadRunDetail}
        />
      )}

      {mainTab === 'detail' && (
        <BacktestDetailPanel
          selectedRunId={selectedRunId}
          detailTab={detailTab}
          onChangeDetailTab={setDetailTab}
          detailLoading={detailLoading}
          runOverview={runOverview}
          runTrades={runTrades}
          runRounds={runRounds}
          runSnapshots={runSnapshots}
          runStrategyConfig={runStrategyConfig}
          onDownloadCsv={downloadCsv}
          apiBaseUrl={API_BASE_URL}
        />
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
