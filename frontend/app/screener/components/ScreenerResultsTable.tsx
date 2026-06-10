'use client'

import { useState, useMemo, useCallback } from 'react'
import Link from 'next/link'
import axios from 'axios'
import { Button, message, Popconfirm, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getAuthHeader } from '../../lib/auth'
import { getApiBaseUrl } from '../../lib/api'
import { marketClassByValue } from '../../lib/marketColors'
import { EmptyState, SurfaceCard } from '../../components/ui-shell'

const API_BASE_URL = getApiBaseUrl()

export interface ScreenerResult {
  symbol: string
  stock_name?: string | null
  signal_strength: number
  reason: string
  current_price?: number | null
  change_pct?: number | null
}

const SIGNAL_DESCRIPTIONS: Record<string, string> = {
  gmm_volume_v1: '密度越低信号越强：当前价格处于历史成交稀疏区，均值回归概率较高。',
  ma_cross_v1: '均线间距越大信号越强：短均线突破长均线的乖离率，趋势动量信号。',
  volume_shrink_drop_v1: '缩量下跌后反弹信号：连续缩量回调后出现企稳迹象。',
}

export function ScreenerResultsTable({
  results,
  status,
  progress,
  strategyId,
}: {
  results: ScreenerResult[]
  status: 'idle' | 'scanning' | 'completed' | 'failed'
  progress: { total: number; scanned: number; hits: number }
  strategyId: string
}) {
  const [addingCodes, setAddingCodes] = useState<Set<string>>(new Set())

  const handleAddToWatchlist = useCallback(async (code: string, name?: string | null) => {
    if (addingCodes.has(code)) return
    setAddingCodes((prev) => new Set(prev).add(code))
    try {
      await axios.post(
        `${API_BASE_URL}/api/watchlist`,
        {
          stock_code: code,
          stock_name: name || null,
          alert_enabled: true,
          alert_threshold: 0.7,
        },
        { headers: getAuthHeader() },
      )
      message.success(`${code} 已添加到监控列表`)
    } catch (err: any) {
      const detail = err.response?.data?.detail || '添加失败'
      if (detail.includes('已在监控列表中')) {
        message.warning(`${code} 已在监控列表中`)
      } else {
        message.error(detail)
      }
    } finally {
      setAddingCodes((prev) => {
        const next = new Set(prev)
        next.delete(code)
        return next
      })
    }
  }, [addingCodes])

  const columns: ColumnsType<ScreenerResult> = useMemo(
    () => [
    {
      title: '代码',
      dataIndex: 'symbol',
      key: 'symbol',
      width: 100,
    },
    {
      title: '名称',
      dataIndex: 'stock_name',
      key: 'stock_name',
      width: 130,
      render: (v) => v || '-',
    },
    {
      title: '最新价',
      dataIndex: 'current_price',
      key: 'current_price',
      width: 95,
      align: 'right',
      render: (v) => (v != null ? `¥${v.toFixed(2)}` : '-'),
    },
    {
      title: '涨跌幅',
      dataIndex: 'change_pct',
      key: 'change_pct',
      width: 90,
      align: 'right',
      sorter: (a, b) => (a.change_pct ?? 0) - (b.change_pct ?? 0),
      render: (v) =>
        v != null ? (
          <span className={marketClassByValue(v)}>
            {v > 0 ? '+' : ''}
            {v.toFixed(2)}%
          </span>
        ) : (
          '-'
        ),
    },
    {
      title: (
        <Tooltip title={SIGNAL_DESCRIPTIONS[strategyId] || '信号越强，触发条件越显著。'}>
          <span className="cursor-help border-b border-dotted border-[var(--text-dim)]">信号强度</span>
        </Tooltip>
      ),
      dataIndex: 'signal_strength',
      key: 'signal_strength',
      width: 110,
      align: 'right',
      sorter: (a, b) => a.signal_strength - b.signal_strength,
      defaultSortOrder: 'descend',
      render: (v) => {
        const pct = Math.min(100, Math.max(0, Math.round(v * 1000)))
        const color =
          v >= 0.05 ? 'red' : v >= 0.02 ? 'orange' : 'blue'
        return (
          <div className="flex items-center gap-2">
            <div className="h-1.5 flex-1 rounded-full bg-[rgba(0,0,0,0.06)]">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${pct}%`,
                  backgroundColor:
                    color === 'red'
                      ? 'var(--up)'
                      : color === 'orange'
                        ? '#fa8c16'
                        : 'var(--brand)',
                }}
              />
            </div>
            <span className="text-xs tabular-nums">{(v * 100).toFixed(2)}%</span>
          </div>
        )
      },
    },
    {
      title: '信号原因',
      dataIndex: 'reason',
      key: 'reason',
      width: 200,
      ellipsis: true,
      render: (v) => (
        <span className="text-sm text-[var(--text-muted)]">{v || '-'}</span>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_, row) => (
        <div className="flex items-center gap-1">
          <Popconfirm
            title={`将 ${row.stock_name || row.symbol} 添加到监控列表？`}
            onConfirm={() => handleAddToWatchlist(row.symbol, row.stock_name)}
          >
            <Button
              type="primary"
              size="small"
              loading={addingCodes.has(row.symbol)}
            >
              添加监控
            </Button>
          </Popconfirm>
          <Link
            href={`/depth?code=${row.symbol}`}
            className="ve-tab-button text-xs px-2 py-1"
          >
            分析
          </Link>
        </div>
      ),
    },
  ],
  [addingCodes, handleAddToWatchlist, strategyId],
)

  const isRunning = status === 'scanning'
  const isDone = status === 'completed'
  const isIdle = status === 'idle'

  return (
    <SurfaceCard
      title={
        isRunning
          ? `扫描进度: ${progress.scanned.toLocaleString()} / ${progress.total.toLocaleString()}`
          : isDone
            ? `选股结果`
            : `选股结果`
      }
      description={
        isRunning
          ? `已命中 ${progress.hits} 个信号`
          : isDone
            ? results.length > 0
              ? `共命中 ${results.length} 个信号，按信号强度排序`
              : '扫描完成，当前市场未触发买入信号'
            : undefined
      }
    >
      {isRunning && progress.total > 0 ? (
        <div className="mb-4">
          <div className="h-2 w-full rounded-full bg-[rgba(0,0,0,0.06)]">
            <div
              className="h-full rounded-full bg-[var(--brand)] transition-all duration-500"
              style={{
                width: `${Math.round((progress.scanned / progress.total) * 100)}%`,
              }}
            />
          </div>
          <div className="mt-2 text-center text-xs text-[var(--text-dim)]">
            {progress.scanned.toLocaleString()} / {progress.total.toLocaleString()}
            {' · '}
            命中 <span className="font-semibold text-[var(--brand-strong)]">{progress.hits}</span> 个信号
          </div>
        </div>
      ) : null}

      {results.length > 0 ? (
        <>
          {/* Desktop table */}
          <div className="hidden md:block">
            <Table<ScreenerResult>
              rowKey="symbol"
              size="small"
              columns={columns}
              dataSource={results}
              pagination={{
                pageSize: 50,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100],
                showTotal: (total) => `共 ${total} 条`,
              }}
              scroll={{ x: 780 }}
            />
          </div>

          {/* Mobile cards */}
          <div className="space-y-3 md:hidden">
            {results.map((item) => (
              <div
                key={item.symbol}
                className="rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.75)] p-4"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-[var(--text-strong)]">
                      {item.stock_name || item.symbol}
                    </div>
                    <div className="text-sm text-[var(--text-dim)]">{item.symbol}</div>
                  </div>
                  <div className="text-right">
                    {item.current_price != null ? (
                      <>
                        <div className="font-semibold text-[var(--text-strong)]">
                          ¥{item.current_price.toFixed(2)}
                        </div>
                        {item.change_pct != null ? (
                          <div className={marketClassByValue(item.change_pct)}>
                            {item.change_pct > 0 ? '+' : ''}
                            {item.change_pct.toFixed(2)}%
                          </div>
                        ) : null}
                      </>
                    ) : (
                      <Tag>无行情</Tag>
                    )}
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-[var(--text-dim)]">信号强度</div>
                    <div className="font-medium text-[var(--text-strong)]">
                      {(item.signal_strength * 100).toFixed(2)}%
                    </div>
                  </div>
                  <div>
                    <div className="text-[var(--text-dim)]">信号原因</div>
                    <div className="font-medium text-[var(--text-strong)] truncate">
                      {item.reason || '-'}
                    </div>
                  </div>
                </div>
                <div className="mt-4 flex items-center gap-2">
                  <Popconfirm
                    title={`将 ${item.stock_name || item.symbol} 添加到监控列表？`}
                    onConfirm={() =>
                      handleAddToWatchlist(item.symbol, item.stock_name)
                    }
                  >
                    <Button
                      type="primary"
                      size="small"
                      loading={addingCodes.has(item.symbol)}
                    >
                      添加监控
                    </Button>
                  </Popconfirm>
                  <Link
                    href={`/depth?code=${item.symbol}`}
                    className="ve-tab-button text-xs px-2 py-1"
                  >
                    分析
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : isRunning ? (
        <EmptyState
          title="正在扫描中…"
          description="系统正在逐只扫描股票，命中信号后将实时展示在这里。"
        />
      ) : isDone ? (
        <EmptyState
          title="扫描完成，未命中信号"
          description={`已扫描 ${progress.total.toLocaleString()} 只股票，当前市场条件下未触发买入信号。可尝试调整策略参数或扩大选股范围后重新扫描。`}
        />
      ) : (
        <EmptyState
          title="开始选股"
          description="选择策略并配置参数后，点击「开始选股」扫描全市场触发买入信号的标的。"
        />
      )}
    </SurfaceCard>
  )
}
