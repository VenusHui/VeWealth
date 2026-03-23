'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { getAuthHeader, isAuthenticated } from '../lib/auth'
import { MainTabSwitcher } from './components/MainTabSwitcher'
import { BacktestRecordsPanel } from './components/BacktestRecordsPanel'
import { BacktestDetailPanel } from './components/BacktestDetailPanel'
import { BacktestCreatePanel } from './components/BacktestCreatePanel'
import { StrategyManagementPanel } from './components/StrategyManagementPanel'
import type {
  BacktestOverview,
  BacktestFacts,
  BacktestResult,
  DetailTab,
  JobItem,
  MainTab,
  RoundRow,
  RunItem,
  SnapshotRow,
  Strategy,
  StrategyConfig,
  StrategyManagementListItem,
  TradeRow,
} from './components/types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

const ACTIVE_JOB_STATUSES = ['pending', 'running'] as const

export default function BacktestPage() {
  const [mainTab, setMainTab] = useState<MainTab>('create')
  const [detailTab, setDetailTab] = useState<DetailTab>('overview')
  const [mounted, setMounted] = useState(false)

  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [strategyParams, setStrategyParams] = useState<Record<string, string>>({})
  const [mode, setMode] = useState<'manual_symbols' | 'strategy_select'>('manual_symbols')
  const [universeType, setUniverseType] = useState<'all' | 'custom'>('all')
  const [symbols, setSymbols] = useState('000001')
  const [poolSymbols, setPoolSymbols] = useState('')
  const [boardFilters, setBoardFilters] = useState<Array<'main' | 'gem' | 'star' | 'bse'>>(['main'])
  const [excludeSt, setExcludeSt] = useState(true)
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
  const [runsTotal, setRunsTotal] = useState(0)
  const [runsPage, setRunsPage] = useState(1)
  const [runsPageSize, setRunsPageSize] = useState(20)
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [runOverview, setRunOverview] = useState<BacktestOverview | null>(null)
  const [runTrades, setRunTrades] = useState<TradeRow[]>([])
  const [runTradesTotal, setRunTradesTotal] = useState(0)
  const [runTradesPage, setRunTradesPage] = useState(1)
  const [runTradesPageSize, setRunTradesPageSize] = useState(20)
  const [runRounds, setRunRounds] = useState<RoundRow[]>([])
  const [runRoundsTotal, setRunRoundsTotal] = useState(0)
  const [runRoundsPage, setRunRoundsPage] = useState(1)
  const [runRoundsPageSize, setRunRoundsPageSize] = useState(20)
  const [runSnapshots, setRunSnapshots] = useState<SnapshotRow[]>([])
  const [runFacts, setRunFacts] = useState<BacktestFacts | null>(null)
  const [snapshotBenchmarkCode, setSnapshotBenchmarkCode] = useState<string | undefined>(undefined)
  const [snapshotCompareRunId, setSnapshotCompareRunId] = useState<number | undefined>(undefined)
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

  const [strategyManagementItems, setStrategyManagementItems] = useState<StrategyManagementListItem[]>([])
  const [strategyManagementLoading, setStrategyManagementLoading] = useState(false)
  const [strategyManagementQuery, setStrategyManagementQuery] = useState('')
  const [strategyManagementUsable, setStrategyManagementUsable] = useState<'all' | 'true' | 'false'>('all')
  const [strategyManagementPage, setStrategyManagementPage] = useState(1)
  const [strategyManagementPageSize, setStrategyManagementPageSize] = useState(20)
  const [strategyManagementTotal, setStrategyManagementTotal] = useState(0)

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_id === strategyId),
    [strategies, strategyId]
  )

  const fetchRuns = useCallback(async (page = runsPage, pageSize = runsPageSize) => {
    if (!isAuthenticated()) return
    setRunsLoading(true)
    try {
      const offset = (page - 1) * pageSize
      const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs?limit=${pageSize}&offset=${offset}`, {
        headers: getAuthHeader(),
      })
      setRuns(resp.data?.data || [])
      setRunsTotal(resp.data?.total || 0)
    } catch {
      // ignore
    } finally {
      setRunsLoading(false)
    }
  }, [runsPage, runsPageSize])

  const fetchJobs = useCallback(async (showLoading = true) => {
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
  }, [])

  const fetchStrategyManagementList = useCallback(async (
    page = strategyManagementPage,
    pageSize = strategyManagementPageSize,
    query = strategyManagementQuery,
    usable = strategyManagementUsable,
  ) => {
    if (!isAuthenticated()) return
    setStrategyManagementLoading(true)
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      params.set('usable', usable)
      params.set('sort_by', 'last_modified_at')
      params.set('sort_order', 'desc')
      if (query.trim()) params.set('query', query.trim())

      const resp = await axios.get(`${API_BASE_URL}/api/backtest/strategy-management/list?${params.toString()}`, {
        headers: getAuthHeader(),
      })
      const list: StrategyManagementListItem[] = Array.isArray(resp.data?.data)
        ? (resp.data.data as StrategyManagementListItem[])
        : []
      setStrategyManagementItems(list)
      setStrategyManagementTotal(Number(resp.data?.total || 0))
    } catch {
      // ignore
    } finally {
      setStrategyManagementLoading(false)
    }
  }, [strategyManagementPage, strategyManagementPageSize, strategyManagementQuery, strategyManagementUsable])

  const loadRunDetail = (runId: number) => {
    setRunOverview(null)
    setRunTrades([])
    setRunTradesTotal(0)
    setRunTradesPage(1)
    setRunRounds([])
    setRunRoundsTotal(0)
    setRunRoundsPage(1)
    setRunSnapshots([])
    setRunFacts(null)
    setSnapshotBenchmarkCode(undefined)
    setSnapshotCompareRunId(undefined)
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

  const loadDetailTabData = useCallback(async (
    runId: number,
    tab: DetailTab,
    page?: number,
    pageSize?: number,
    snapshotOptions?: { benchmarkCode?: string; compareRunId?: number },
  ) => {
    const headers = getAuthHeader()
    setDetailLoading((prev) => ({ ...prev, [tab]: true }))

    try {
      if (tab === 'overview') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/overview`, { headers })
        setRunOverview(resp.data?.data || null)
      }
      if (tab === 'trades') {
        const p = page ?? runTradesPage
        const ps = pageSize ?? runTradesPageSize
        const offset = (p - 1) * ps
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/trades?limit=${ps}&offset=${offset}`, { headers })
        setRunTrades(resp.data?.data || [])
        setRunTradesTotal(resp.data?.total || 0)
      }
      if (tab === 'rounds') {
        const p = page ?? runRoundsPage
        const ps = pageSize ?? runRoundsPageSize
        const offset = (p - 1) * ps
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/rounds?limit=${ps}&offset=${offset}`, { headers })
        setRunRounds(resp.data?.data || [])
        setRunRoundsTotal(resp.data?.total || 0)
      }
      if (tab === 'snapshots') {
        const benchmarkCode = snapshotOptions?.benchmarkCode
        const compareRunId = snapshotOptions?.compareRunId
        const params = new URLSearchParams()
        if (benchmarkCode) params.set('benchmark_code', benchmarkCode)
        if (compareRunId) params.set('compare_run_id', String(compareRunId))
        const qs = params.toString()
        const factsUrl = `${API_BASE_URL}/api/backtest/runs/${runId}/facts${qs ? `?${qs}` : ''}`
        const [factsResp, snapshotsResp] = await Promise.all([
          axios.get(factsUrl, { headers }),
          axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/snapshots?limit=10000&offset=0`, { headers }),
        ])
        setRunFacts(factsResp.data?.data || null)
        setRunSnapshots(snapshotsResp.data?.data || [])
      }
      if (tab === 'strategy') {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/runs/${runId}/strategy-config`, { headers })
        setRunStrategyConfig(resp.data?.data || null)
      }
    } finally {
      setDetailLoading((prev) => ({ ...prev, [tab]: false }))
      setDetailLoaded((prev) => ({ ...prev, [tab]: true }))
    }
  }, [runRoundsPage, runRoundsPageSize, runTradesPage, runTradesPageSize])

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
    setMounted(true)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const tab = new URLSearchParams(window.location.search).get('tab')
    if (tab === 'strategies') {
      setMainTab('strategies')
    }
  }, [])

  useEffect(() => {
    if (!mounted) return
    if (!isAuthenticated()) return

    const fetchStrategies = async () => {
      try {
        const resp = await axios.get(`${API_BASE_URL}/api/backtest/strategies`, {
          headers: getAuthHeader(),
        })
        const list: Strategy[] = Array.isArray(resp.data?.data)
          ? (resp.data.data as Strategy[])
          : []
        setStrategies(list)
        if (list.length > 0) {
          setStrategyId(list[0].strategy_id)
          const defaults: Record<string, string> = {}
          list[0].param_schema.forEach((p: Strategy['param_schema'][number]) => {
            defaults[p.key] = String(p.default ?? '')
          })
          setStrategyParams(defaults)
        }
      } catch {
        setError('加载策略列表失败')
      }
    }

    fetchStrategies()
    fetchJobs()
  }, [fetchJobs, mounted])

  useEffect(() => {
    if (!mounted) return
    fetchRuns(runsPage, runsPageSize)
  }, [fetchRuns, runsPage, runsPageSize, mounted])

  useEffect(() => {
    if (!mounted) return
    fetchStrategyManagementList(
      strategyManagementPage,
      strategyManagementPageSize,
      strategyManagementQuery,
      strategyManagementUsable,
    )
  }, [
    mounted,
    fetchStrategyManagementList,
    strategyManagementPage,
    strategyManagementPageSize,
    strategyManagementQuery,
    strategyManagementUsable,
  ])

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
  }, [fetchJobs, mainTab, jobs, job])

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
  }, [fetchJobs, fetchRuns, job?.job_id])

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
      loadDetailTabData(selectedRunId, 'snapshots', undefined, undefined, {
        benchmarkCode: snapshotBenchmarkCode,
        compareRunId: snapshotCompareRunId,
      })
      return
    }
    if (detailTab === 'strategy' && !detailLoaded.strategy && !detailLoading.strategy) {
      loadDetailTabData(selectedRunId, 'strategy')
    }
  }, [mainTab, selectedRunId, detailTab, detailLoaded, detailLoading, loadDetailTabData, snapshotBenchmarkCode, snapshotCompareRunId])

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

      if (mode === 'strategy_select' && boardFilters.length === 0) {
        setError('请至少选择一个板块')
        return
      }

      if (mode === 'strategy_select') {
        castParams.boards = boardFilters
        castParams.exclude_st = excludeSt
      }

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

  const changeTradesPage = (page: number) => {
    if (!selectedRunId) return
    setRunTradesPage(page)
    loadDetailTabData(selectedRunId, 'trades', page, runTradesPageSize)
  }

  const changeTradesPageSize = (size: number) => {
    if (!selectedRunId) return
    setRunTradesPageSize(size)
    setRunTradesPage(1)
    loadDetailTabData(selectedRunId, 'trades', 1, size)
  }

  const changeRoundsPage = (page: number) => {
    if (!selectedRunId) return
    setRunRoundsPage(page)
    loadDetailTabData(selectedRunId, 'rounds', page, runRoundsPageSize)
  }

  const changeRoundsPageSize = (size: number) => {
    if (!selectedRunId) return
    setRunRoundsPageSize(size)
    setRunRoundsPage(1)
    loadDetailTabData(selectedRunId, 'rounds', 1, size)
  }

  const changeSnapshotComparison = (benchmarkCode?: string, compareRunId?: number) => {
    if (!selectedRunId) return
    setSnapshotBenchmarkCode(benchmarkCode)
    setSnapshotCompareRunId(compareRunId)
    loadDetailTabData(selectedRunId, 'snapshots', undefined, undefined, {
      benchmarkCode,
      compareRunId,
    })
  }

  if (!mounted) {
    return <div className="max-w-3xl mx-auto py-10 px-4">加载中...</div>
  }

  if (!isAuthenticated()) {
    return <div className="max-w-3xl mx-auto py-10 px-4">请先登录后使用回测功能。</div>
  }

  return (
    <div className="app-page-shell">
      <div className="app-page-container app-section-stack">
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
          boardFilters={boardFilters}
          excludeSt={excludeSt}
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
          onBoardFilterChange={(board, checked) => {
            setBoardFilters((prev) => {
              if (checked) {
                if (prev.includes(board)) return prev
                return [...prev, board]
              }
              return prev.filter((b) => b !== board)
            })
          }}
          onExcludeStChange={setExcludeSt}
          onStrategyParamChange={(k, v) => setStrategyParams((prev) => ({ ...prev, [k]: v }))}
          onSubmit={handleRun}
        />
      )}

      {mainTab === 'records' && (
        <BacktestRecordsPanel
          runs={runs}
          runsLoading={runsLoading}
          onRefresh={() => fetchRuns(runsPage, runsPageSize)}
          onViewDetail={loadRunDetail}
          total={runsTotal}
          page={runsPage}
          pageSize={runsPageSize}
          onPageChange={setRunsPage}
          onPageSizeChange={(size) => {
            setRunsPageSize(size)
            setRunsPage(1)
          }}
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
          runFacts={runFacts}
          allRuns={runs}
          benchmarkCode={snapshotBenchmarkCode}
          compareRunId={snapshotCompareRunId}
          onChangeSnapshotComparison={changeSnapshotComparison}
          runStrategyConfig={runStrategyConfig}
          onDownloadCsv={downloadCsv}
          apiBaseUrl={API_BASE_URL}
          tradesTotal={runTradesTotal}
          tradesPage={runTradesPage}
          tradesPageSize={runTradesPageSize}
          onTradesPageChange={changeTradesPage}
          onTradesPageSizeChange={changeTradesPageSize}
          roundsTotal={runRoundsTotal}
          roundsPage={runRoundsPage}
          roundsPageSize={runRoundsPageSize}
          onRoundsPageChange={changeRoundsPage}
          onRoundsPageSizeChange={changeRoundsPageSize}
        />
      )}

      {mainTab === 'strategies' && (
        <StrategyManagementPanel
          loading={strategyManagementLoading}
          items={strategyManagementItems}
          query={strategyManagementQuery}
          usableFilter={strategyManagementUsable}
          total={strategyManagementTotal}
          page={strategyManagementPage}
          pageSize={strategyManagementPageSize}
          onRefresh={() => fetchStrategyManagementList(strategyManagementPage, strategyManagementPageSize, strategyManagementQuery, strategyManagementUsable)}
          onQueryChange={(value) => {
            setStrategyManagementQuery(value)
            setStrategyManagementPage(1)
          }}
          onUsableFilterChange={(value) => {
            setStrategyManagementUsable(value)
            setStrategyManagementPage(1)
          }}
          onPageChange={setStrategyManagementPage}
          onPageSizeChange={(size) => {
            setStrategyManagementPageSize(size)
            setStrategyManagementPage(1)
          }}
        />
      )}

      {result && (
        <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-xl px-4 py-3 text-sm">
          最新任务完成：Run #{result.run_id}，交易笔数 {result.trades?.length || 0}。
          <button className="ml-3 text-emerald-800 underline" onClick={() => mainTab !== 'records' && setMainTab('records')}>去记录页查看</button>
        </div>
      )}
      </div>
    </div>
  )
}
