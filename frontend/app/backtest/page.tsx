'use client'

import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { getAuthHeader, isAuthenticated } from '../lib/auth'
import { MainTabSwitcher } from './components/MainTabSwitcher'
import { BacktestRecordsPanel } from './components/BacktestRecordsPanel'
import { BacktestDetailPanel } from './components/BacktestDetailPanel'
import { BacktestCreatePanel } from './components/BacktestCreatePanel'
import type {
  BacktestOverview,
  BacktestResult,
  DetailTab,
  JobItem,
  MainTab,
  RoundRow,
  RunItem,
  SnapshotRow,
  Strategy,
  StrategyConfig,
  TradeRow,
} from './components/types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

const ACTIVE_JOB_STATUSES = ['pending', 'running'] as const

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

  const [job, setJob] = useState<JobItem | null>(null)
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [jobsLoading, setJobsLoading] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)

  const [runs, setRuns] = useState<RunItem[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [runOverview, setRunOverview] = useState<BacktestOverview | null>(null)
  const [runTrades, setRunTrades] = useState<TradeRow[]>([])
  const [runRounds, setRunRounds] = useState<RoundRow[]>([])
  const [runSnapshots, setRunSnapshots] = useState<SnapshotRow[]>([])
  const [runStrategyConfig, setRunStrategyConfig] = useState<StrategyConfig | null>(null)
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
          list[0].param_schema.forEach((p) => {
            defaults[p.key] = String(p.default ?? '')
          })
          setStrategyParams(defaults)
        }
      } catch {
        setError('加载策略列表失败')
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

      const castParams: Record<string, unknown> = {}
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
    } catch {
      setError('回测执行失败')
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
        <BacktestCreatePanel
          name={name}
          strategyId={strategyId}
          strategies={strategies}
          mode={mode}
          universeType={universeType}
          symbols={symbols}
          poolSymbols={poolSymbols}
          initialCash={initialCash}
          startDate={startDate}
          endDate={endDate}
          selectedStrategy={selectedStrategy}
          strategyParams={strategyParams}
          loading={loading}
          error={error}
          job={job}
          jobs={jobs}
          jobsLoading={jobsLoading}
          onNameChange={setName}
          onStrategyChange={setStrategyId}
          onModeChange={setMode}
          onUniverseTypeChange={setUniverseType}
          onSymbolsChange={setSymbols}
          onPoolSymbolsChange={setPoolSymbols}
          onInitialCashChange={setInitialCash}
          onStartDateChange={setStartDate}
          onEndDateChange={setEndDate}
          onStrategyParamChange={(k, v) => setStrategyParams((prev) => ({ ...prev, [k]: v }))}
          onSubmit={handleRun}
        />
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
