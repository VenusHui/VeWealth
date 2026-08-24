'use client'

import { useState, useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { Alert } from 'antd'
import { getAuthHeader, isAuthenticated } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'
import { AppPage, MetricCard } from '../components/ui-shell'
import { ScreenerConfigPanel } from './components/ScreenerConfigPanel'
import {
  ScreenerResultsTable,
  type ScreenerResult,
} from './components/ScreenerResultsTable'
import type { Strategy } from '../backtest/components/types'

const API_BASE_URL = getApiBaseUrl()

interface ScanState {
  scan_id: string
  status: 'idle' | 'scanning' | 'completed' | 'failed'
  progress: { total: number; scanned: number; hits: number }
  results: ScreenerResult[]
  error?: string | null
}

export default function ScreenerPage() {
  const router = useRouter()
  const [mounted, setMounted] = useState(false)

  // Strategy state
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [strategyParams, setStrategyParams] = useState<Record<string, string>>({})
  const [boardFilters, setBoardFilters] = useState<Array<'main' | 'gem' | 'star' | 'bse'>>(['main'])
  const [excludeSt, setExcludeSt] = useState(true)
  const [error, setError] = useState('')

  // Scan state
  const [scan, setScan] = useState<ScanState>({
    scan_id: '',
    status: 'idle',
    progress: { total: 0, scanned: 0, hits: 0 },
    results: [],
  })

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.strategy_id === strategyId),
    [strategies, strategyId],
  )

  // Fetch strategies on mount
  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (!mounted || !isAuthenticated()) return
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
  }, [mounted])

  // Sync param defaults when strategy changes
  useEffect(() => {
    if (!selectedStrategy) return
    const defaults: Record<string, string> = {}
    selectedStrategy.param_schema.forEach((p) => {
      defaults[p.key] = String(p.default ?? '')
    })
    setStrategyParams(defaults)
  }, [selectedStrategy])

  // Poll scan status
  useEffect(() => {
    if (scan.status !== 'scanning' || !scan.scan_id) return
    const timer = setInterval(async () => {
      try {
        const resp = await axios.get(
          `${API_BASE_URL}/api/screener/scans/${scan.scan_id}`,
          { headers: getAuthHeader() },
        )
        const data = resp.data
        setScan({
          scan_id: data.scan_id,
          status: data.status,
          progress: data.progress || { total: 0, scanned: 0, hits: 0 },
          results: data.results || [],
          error: data.error,
        })
      } catch {
        // ignore polling errors
      }
    }, 2500)
    return () => clearInterval(timer)
  }, [scan.status, scan.scan_id])

  const handleStartScan = async () => {
    if (!isAuthenticated()) {
      router.push('/login')
      return
    }
    try {
      setError('')
      setScan({
        scan_id: '',
        status: 'scanning',
        progress: { total: 0, scanned: 0, hits: 0 },
        results: [],
      })

      const castParams: Record<string, unknown> = {}
      Object.keys(strategyParams).forEach((k) => {
        const val = strategyParams[k]
        castParams[k] = /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val
      })

      const resp = await axios.post(
        `${API_BASE_URL}/api/screener/scan`,
        {
          strategy_id: strategyId,
          strategy_params: castParams,
          boards: boardFilters,
          exclude_st: excludeSt,
        },
        { headers: getAuthHeader() },
      )

      const data = resp.data
      setScan({
        scan_id: data.scan_id,
        status: data.status,
        progress: data.progress || { total: 0, scanned: 0, hits: 0 },
        results: data.results || [],
        error: data.error,
      })
    } catch (err: any) {
      const msg = err.response?.data?.detail || '启动扫描失败'
      setError(msg)
      setScan((prev) => ({ ...prev, status: 'failed', error: msg }))
    }
  }

  if (!mounted) {
    return <div className="mx-auto max-w-3xl px-4 py-10">加载中...</div>
  }

  if (!isAuthenticated()) {
    return <div className="mx-auto max-w-3xl px-4 py-10">请先登录后使用选股功能。</div>
  }

  return (
    <AppPage>
      {/* Metric cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard
          label="可选策略"
          value={strategies.length.toLocaleString()}
          meta="已注册的选股策略"
          tone="brand"
          icon="◎"
        />
        <MetricCard
          label="扫描标的"
          value={
            scan.status === 'scanning'
              ? scan.progress.scanned.toLocaleString()
              : scan.progress.total.toLocaleString()
          }
          meta={scan.status === 'completed' ? '已完成扫描' : scan.status === 'scanning' ? '扫描中…' : '待扫描'}
          icon="▤"
        />
        <MetricCard
          label="命中信号"
          value={scan.progress.hits.toLocaleString()}
          meta={
            scan.status === 'completed'
              ? '当前触发买入信号'
              : scan.status === 'scanning'
                ? '实时更新'
                : '—'
          }
          tone="positive"
          icon="▲"
        />
      </div>

      {/* Config panel */}
      <ScreenerConfigPanel
        strategyId={strategyId}
        strategies={strategies}
        selectedStrategy={selectedStrategy}
        strategyParams={strategyParams}
        boardFilters={boardFilters}
        excludeSt={excludeSt}
        scanning={scan.status === 'scanning'}
        onStrategyChange={setStrategyId}
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
        onStrategyParamChange={(k, v) =>
          setStrategyParams((prev) => ({ ...prev, [k]: v }))
        }
        onStartScan={handleStartScan}
      />

      {/* Error */}
      {error ? (
        <Alert type="error" message={error} closable onClose={() => setError('')} />
      ) : null}

      {/* Scan failed */}
      {scan.status === 'failed' ? (
        <Alert
          type="error"
          message="扫描失败"
          description={scan.error || '未知错误'}
          closable
        />
      ) : null}

      {/* Results table */}
      <ScreenerResultsTable
        results={scan.results}
        status={scan.status}
        progress={scan.progress}
        strategyId={strategyId}
      />
    </AppPage>
  )
}
