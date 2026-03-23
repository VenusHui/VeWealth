import { Component, type ReactNode, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Select,
  Slider,
  Space,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'
import { LoadingHint } from './LoadingHint'
import { BacktestTable } from './BacktestTable'
import type {
  BacktestOverview,
  DetailTab,
  RoundRow,
  RunItem,
  SnapshotHolding,
  SnapshotRow,
  StrategyConfig,
  TradeRow,
  BacktestFacts,
} from './types'

const detailTabs: { key: DetailTab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'trades', label: '成交明细' },
  { key: 'rounds', label: '回合交易' },
  { key: 'snapshots', label: '持仓快照' },
  { key: 'strategy', label: '策略配置' },
]

const benchmarkOptions = [
  { label: '上证综指', value: '000001.SH' },
  { label: '深证成指', value: '399001.SZ' },
  { label: '创业板指', value: '399006.SZ' },
  { label: '沪深300', value: '000300.SH' },
]

class SnapshotErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error: unknown) {
    // eslint-disable-next-line no-console
    console.error('Snapshot panel render error', error)
  }

  render() {
    if (this.state.hasError) {
      return <Alert type="error" message="快照图表渲染异常，请刷新页面重试" />
    }
    return this.props.children
  }
}

function fmtPct(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  return `${(num * 100).toFixed(2)}%`
}

function fmtSymbolLabel(symbol?: string, stockName?: string): string {
  const code = symbol || '-'
  if (stockName) return `${stockName} (${code})`
  return code
}

const tradeColumns: ColumnsType<TradeRow> = [
  { title: '时间', dataIndex: 'datetime', key: 'datetime', width: 180, ellipsis: true },
  {
    title: '标的',
    dataIndex: 'symbol',
    key: 'symbol',
    width: 170,
    render: (_, r) => fmtSymbolLabel(r.symbol, r.stock_name),
  },
  { title: '方向', dataIndex: 'side', key: 'side', width: 80 },
  { title: '价格', dataIndex: 'price', key: 'price', width: 100, align: 'right' },
  { title: '数量', dataIndex: 'qty', key: 'qty', width: 90, align: 'right' },
  { title: '金额', dataIndex: 'amount', key: 'amount', width: 120, align: 'right' },
  { title: '手续费', dataIndex: 'fee', key: 'fee', width: 100, align: 'right' },
  { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true, render: (v) => v || '-' },
]

const roundColumns: ColumnsType<RoundRow> = [
  {
    title: '标的',
    dataIndex: 'symbol',
    key: 'symbol',
    width: 170,
    render: (_, r) => fmtSymbolLabel(r.symbol, r.stock_name),
  },
  {
    title: '开仓',
    key: 'open',
    width: 220,
    ellipsis: true,
    render: (_, r) => `${r.open_time || '-'} @ ${r.open_price ?? '-'}`,
  },
  {
    title: '平仓',
    key: 'close',
    width: 220,
    ellipsis: true,
    render: (_, r) => `${r.close_time || '-'} @ ${r.close_price ?? '-'}`,
  },
  { title: '持有天数', dataIndex: 'holding_days', key: 'holding_days', width: 100, align: 'right' },
  {
    title: '收益率',
    dataIndex: 'pnl_ratio',
    key: 'pnl_ratio',
    width: 100,
    align: 'right',
    render: (v) => fmtPct(v),
  },
  { title: '盈亏', dataIndex: 'pnl_amount', key: 'pnl_amount', width: 120, align: 'right' },
  { title: '退出原因', dataIndex: 'exit_reason', key: 'exit_reason', ellipsis: true, render: (v) => v || '-' },
]

const holdingColumns: ColumnsType<SnapshotHolding> = [
  {
    title: '标的',
    dataIndex: 'symbol',
    key: 'symbol',
    width: 170,
    render: (_, r) => fmtSymbolLabel(r.symbol, r.stock_name),
  },
  { title: '数量', dataIndex: 'qty', key: 'qty', align: 'right', width: 90 },
  { title: '现价', dataIndex: 'last_price', key: 'last_price', align: 'right', width: 90 },
  { title: '市值', dataIndex: 'market_value', key: 'market_value', align: 'right', width: 120 },
  { title: '权重', dataIndex: 'weight', key: 'weight', align: 'right', width: 90 },
  {
    title: '状态',
    dataIndex: 'position_status',
    key: 'position_status',
    width: 100,
    render: (v) => (v === 'closed_today' ? <Tag color="gold">当日清仓</Tag> : '-'),
  },
]

export function BacktestDetailPanel({
  selectedRunId,
  detailTab,
  onChangeDetailTab,
  detailLoading,
  runOverview,
  runTrades,
  runRounds,
  runSnapshots,
  runFacts,
  allRuns,
  benchmarkCode,
  compareRunId,
  onChangeSnapshotComparison,
  runStrategyConfig,
  onDownloadCsv,
  apiBaseUrl,
  tradesTotal,
  tradesPage,
  tradesPageSize,
  onTradesPageChange,
  onTradesPageSizeChange,
  roundsTotal,
  roundsPage,
  roundsPageSize,
  onRoundsPageChange,
  onRoundsPageSizeChange,
}: {
  selectedRunId: number | null
  detailTab: DetailTab
  onChangeDetailTab: (tab: DetailTab) => void
  detailLoading: Record<DetailTab, boolean>
  runOverview: BacktestOverview | null
  runTrades: TradeRow[]
  runRounds: RoundRow[]
  runSnapshots: SnapshotRow[]
  runFacts: BacktestFacts | null
  allRuns: RunItem[]
  benchmarkCode?: string
  compareRunId?: number
  onChangeSnapshotComparison: (benchmarkCode?: string, compareRunId?: number) => void
  runStrategyConfig: StrategyConfig | null
  onDownloadCsv: (url: string, filename: string) => void
  apiBaseUrl: string
  tradesTotal: number
  tradesPage: number
  tradesPageSize: number
  onTradesPageChange: (page: number) => void
  onTradesPageSizeChange: (size: number) => void
  roundsTotal: number
  roundsPage: number
  roundsPageSize: number
  onRoundsPageChange: (page: number) => void
  onRoundsPageSizeChange: (size: number) => void
}) {
  const filterSummary = (runStrategyConfig?.filter_summary as Record<string, unknown>) || {}
  const symbols = (runStrategyConfig?.symbols as Record<string, unknown>) || {}

  const snapshotItems = useMemo(() => runSnapshots || [], [runSnapshots])
  const factsCurve = useMemo(() => runFacts?.equity_curve_daily || [], [runFacts])
  const factsPositions = useMemo(() => runFacts?.positions_daily_eod || [], [runFacts])
  const snapshotDateItems = useMemo(() => {
    const dates = Array.from(new Set(factsCurve.map((x) => x.trade_date).filter(Boolean))).sort()
    return dates
  }, [factsCurve])
  const [snapshotIndex, setSnapshotIndex] = useState(0)

  useEffect(() => {
    if (snapshotDateItems.length > 0) {
      setSnapshotIndex(snapshotDateItems.length - 1)
      return
    }
    if (snapshotItems.length === 0) {
      setSnapshotIndex(0)
      return
    }
    setSnapshotIndex(snapshotItems.length - 1)
  }, [snapshotDateItems.length, snapshotItems.length])

  const hasFactsTimeline = snapshotDateItems.length > 0
  const benchmarkByDate = useMemo(() => {
    const map = new Map<string, number>()
    for (const p of runFacts?.benchmark_curve_daily || []) {
      if (p.trade_date && p.value_norm != null) map.set(p.trade_date, Number(p.value_norm))
    }
    return map
  }, [runFacts])
  const compareByDate = useMemo(() => {
    const map = new Map<string, number>()
    for (const p of runFacts?.compare_run_curve_daily || []) {
      if (p.trade_date && p.value_norm != null) map.set(p.trade_date, Number(p.value_norm))
    }
    return map
  }, [runFacts])
  const mainNormByDate = useMemo(() => {
    const map = new Map<string, number>()
    const first = factsCurve.find((x) => Number(x.equity) > 0)
    const base = first ? Number(first.equity) : 0
    for (const p of factsCurve) {
      if (base > 0) map.set(p.trade_date, Number((Number(p.equity) / base).toFixed(6)))
    }
    return map
  }, [factsCurve])
  const comparisonChartData = useMemo(() => {
    return snapshotDateItems
      .map((d) => {
        const mainNorm = mainNormByDate.get(d)
        const benchmarkNorm = benchmarkByDate.get(d)
        const compareNorm = compareByDate.get(d)
        return {
          trade_date: d,
          main_norm: Number.isFinite(mainNorm as number) ? (mainNorm as number) : null,
          benchmark_norm: Number.isFinite(benchmarkNorm as number) ? (benchmarkNorm as number) : null,
          compare_norm: Number.isFinite(compareNorm as number) ? (compareNorm as number) : null,
        }
      })
      .filter((row) => row.main_norm != null)
  }, [snapshotDateItems, mainNormByDate, benchmarkByDate, compareByDate])

  const timelineDates = useMemo(
    () => (hasFactsTimeline ? comparisonChartData.map((x) => x.trade_date) : snapshotItems.map((x) => x.snapshot_time || '')),
    [hasFactsTimeline, comparisonChartData, snapshotItems]
  )
  const maxTimelineIndex = Math.max(timelineDates.length - 1, 0)
  const safeSnapshotIndex = Math.min(Math.max(snapshotIndex, 0), maxTimelineIndex)

  const currentTradeDate = timelineDates[safeSnapshotIndex]
  const currentCurvePoint = factsCurve.find((x) => x.trade_date === currentTradeDate)

  const compareRunOptions = useMemo(
    () => (allRuns || [])
      .filter((r) => selectedRunId == null || r.id !== selectedRunId)
      .map((r) => ({ label: `#${r.id} ${r.name}`, value: r.id })),
    [allRuns, selectedRunId]
  )

  const currentSnapshotHoldings = useMemo(() => {
    if (!currentTradeDate) return []
    return factsPositions
      .filter((x) => x.trade_date === currentTradeDate)
      .sort((a, b) => {
        const sa = a.position_status === 'closed_today' ? 1 : 0
        const sb = b.position_status === 'closed_today' ? 1 : 0
        if (sa !== sb) return sa - sb
        return Number(b.market_value || 0) - Number(a.market_value || 0)
      })
      .map((x) => ({ ...x, trade_date: currentTradeDate }))
  }, [factsPositions, currentTradeDate])

  const currentSnapshot = snapshotItems[safeSnapshotIndex]

  return (
    <Card title={`回测详情 ${selectedRunId ? `(Run #${selectedRunId})` : ''}`}>
      {!selectedRunId ? (
        <Alert type="info" message="请先在「回测记录」中选择一条记录" />
      ) : (
        <Space direction="vertical" size={16} className="w-full">
          <Space wrap>
            {detailTabs.map((tab) => (
              <Button key={tab.key} type={detailTab === tab.key ? 'primary' : 'default'} onClick={() => onChangeDetailTab(tab.key)}>
                {tab.label}
              </Button>
            ))}
          </Space>

          {detailTab === 'overview' && (
            detailLoading.overview ? (
              <LoadingHint text="概览数据加载中..." />
            ) : (
              <Space direction="vertical" size={16} className="w-full">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {Object.entries(runOverview?.summary || {})
                    .filter(([k]) => !['positions_snapshot', 'final_positions'].includes(k))
                    .slice(0, 8)
                    .map(([k, v]) => {
                      const isPct = /return|drawdown|rate|ratio/i.test(k)
                      return (
                        <Card key={k} size="small">
                          <div className="text-xs text-gray-500">{k}</div>
                          <div className="font-semibold">{isPct ? fmtPct(v) : String(v)}</div>
                        </Card>
                      )
                    })}
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
              </Space>
            )
          )}

          {detailTab === 'trades' && (
            detailLoading.trades ? (
              <LoadingHint text="成交明细加载中..." />
            ) : (
              <Space direction="vertical" size={12} className="w-full">
                <Button onClick={() => onDownloadCsv(`${apiBaseUrl}/api/backtest/runs/${selectedRunId}/trades/export`, `backtest_run_${selectedRunId}_trades.csv`)}>导出成交 CSV</Button>
                <div className="hidden md:block">
                  <BacktestTable<TradeRow>
                    rowKey={(record, index) => `${record.datetime || ''}-${record.symbol || ''}-${index}`}
                    columns={tradeColumns}
                    dataSource={runTrades}
                    locale={{ emptyText: '暂无成交数据' }}
                    scroll={{ x: 1080, y: 460 }}
                    pagination={{
                      current: tradesPage,
                      pageSize: tradesPageSize,
                      total: tradesTotal,
                      showSizeChanger: true,
                      pageSizeOptions: [20, 50, 100],
                      onChange: (page, pageSize) => {
                        if (pageSize !== tradesPageSize) onTradesPageSizeChange(pageSize)
                        onTradesPageChange(page)
                      },
                      showTotal: (total) => `共 ${total} 条`,
                    }}
                  />
                </div>
                <div className="md:hidden space-y-2">
                  {runTrades.map((r, idx) => (
                    <Card key={`${r.datetime || ''}-${r.symbol || ''}-${idx}`} size="small">
                      <div className="space-y-1 text-sm">
                        <div className="flex items-center justify-between">
                          <span>{fmtSymbolLabel(r.symbol, r.stock_name)}</span>
                          <Tag color={r.side === 'buy' ? 'green' : 'red'}>{r.side || '-'}</Tag>
                        </div>
                        <div>{r.datetime || '-'}</div>
                        <div>价格/数量：{r.price ?? '-'} / {r.qty ?? '-'}</div>
                        <div>金额/手续费：{r.amount ?? '-'} / {r.fee ?? '-'}</div>
                        <div className="text-gray-500">{r.reason || '-'}</div>
                      </div>
                    </Card>
                  ))}
                  {runTrades.length === 0 && <div className="text-sm text-gray-500">暂无成交数据</div>}
                </div>
              </Space>
            )
          )}

          {detailTab === 'rounds' && (
            detailLoading.rounds ? (
              <LoadingHint text="回合交易加载中..." />
            ) : (
              <Space direction="vertical" size={12} className="w-full">
                <Button onClick={() => onDownloadCsv(`${apiBaseUrl}/api/backtest/runs/${selectedRunId}/rounds/export`, `backtest_run_${selectedRunId}_rounds.csv`)}>导出回合 CSV</Button>
                <div className="hidden md:block">
                  <BacktestTable<RoundRow>
                    rowKey={(record, index) => `${record.symbol || ''}-${record.open_time || ''}-${index}`}
                    columns={roundColumns}
                    dataSource={runRounds}
                    locale={{ emptyText: '暂无回合交易数据' }}
                    scroll={{ x: 1080, y: 460 }}
                    pagination={{
                      current: roundsPage,
                      pageSize: roundsPageSize,
                      total: roundsTotal,
                      showSizeChanger: true,
                      pageSizeOptions: [20, 50, 100],
                      onChange: (page, pageSize) => {
                        if (pageSize !== roundsPageSize) onRoundsPageSizeChange(pageSize)
                        onRoundsPageChange(page)
                      },
                      showTotal: (total) => `共 ${total} 条`,
                    }}
                  />
                </div>
                <div className="md:hidden space-y-2">
                  {runRounds.map((r, idx) => (
                    <Card key={`${r.symbol || ''}-${r.open_time || ''}-${idx}`} size="small">
                      <div className="space-y-1 text-sm">
                        <div className="font-medium">{fmtSymbolLabel(r.symbol, r.stock_name)}</div>
                        <div>开仓：{r.open_time || '-'} @ {r.open_price ?? '-'}</div>
                        <div>平仓：{r.close_time || '-'} @ {r.close_price ?? '-'}</div>
                        <div>持有天数：{r.holding_days ?? '-'}</div>
                        <div>收益率/盈亏：{fmtPct(r.pnl_ratio)} / {r.pnl_amount ?? '-'}</div>
                        <div className="text-gray-500">退出原因：{r.exit_reason || '-'}</div>
                      </div>
                    </Card>
                  ))}
                  {runRounds.length === 0 && <div className="text-sm text-gray-500">暂无回合交易数据</div>}
                </div>
              </Space>
            )
          )}

          {detailTab === 'snapshots' && (
            <Space direction="vertical" size={12} className="w-full">
              {detailLoading.snapshots ? (
                <LoadingHint text="持仓快照加载中..." />
              ) : snapshotDateItems.length === 0 && snapshotItems.length === 0 ? (
                <Empty description="暂无持仓快照数据" />
              ) : (
                <SnapshotErrorBoundary>
                <Card size="small" title={`快照时间：${currentTradeDate || currentSnapshot?.snapshot_time || '-'}`}>
                  <Space direction="vertical" size={12} className="w-full">
                    <Space wrap>
                      <Select
                        allowClear
                        style={{ width: 220 }}
                        placeholder="指数对比"
                        options={benchmarkOptions}
                        value={benchmarkCode}
                        onChange={(value) => onChangeSnapshotComparison(value, compareRunId)}
                      />
                      <Select
                        allowClear
                        style={{ width: 280 }}
                        placeholder="策略对比（单选）"
                        options={compareRunOptions}
                        value={compareRunId}
                        onChange={(value) => onChangeSnapshotComparison(benchmarkCode, value)}
                      />
                      {runFacts?.benchmark_meta?.source_type ? (
                        <Tag color={runFacts?.benchmark_meta?.source_type === 'tr' ? 'green' : 'orange'}>
                          指数口径：{String(runFacts?.benchmark_meta?.source_type || '').toUpperCase()}
                        </Tag>
                      ) : null}
                    </Space>
                    {runFacts?.benchmark_meta?.source_note ? (
                      <Alert type="warning" message={runFacts.benchmark_meta.source_note} />
                    ) : null}
                    <div className="h-[300px] border rounded-lg p-2">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={comparisonChartData} margin={{ top: 10, right: 20, left: 20, bottom: 10 }}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="trade_date" tick={{ fontSize: 11 }} minTickGap={24} />
                          <YAxis tick={{ fontSize: 11 }} domain={[0.8, 'auto']} />
                          <Tooltip />
                          <Line type="monotone" dataKey="main_norm" name="主策略" stroke="#4f46e5" strokeWidth={2} dot={false} />
                          <Line type="monotone" dataKey="benchmark_norm" name="指数对比" stroke="#f59e0b" strokeWidth={2} dot={false} connectNulls={false} />
                          <Line type="monotone" dataKey="compare_norm" name="策略对比" stroke="#10b981" strokeWidth={2} dot={false} connectNulls={false} />
                          {currentTradeDate ? <ReferenceLine x={currentTradeDate} stroke="#ef4444" strokeDasharray="4 4" /> : null}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                    <Slider
                      min={0}
                      max={maxTimelineIndex}
                      value={safeSnapshotIndex}
                      onChange={(value) => setSnapshotIndex(Number(value))}
                      tooltip={{
                        formatter: (value) =>
                          hasFactsTimeline
                            ? snapshotDateItems[Number(value || 0)] || ''
                            : snapshotItems[Number(value || 0)]?.snapshot_time || '',
                      }}
                    />
                    <Space>
                      <Button
                        size="small"
                        disabled={safeSnapshotIndex <= 0}
                        onClick={() => setSnapshotIndex((prev) => Math.max(prev - 1, 0))}
                      >上一帧</Button>
                      <Button
                        size="small"
                        disabled={safeSnapshotIndex >= maxTimelineIndex}
                        onClick={() => setSnapshotIndex((prev) => Math.min(prev + 1, maxTimelineIndex))}
                      >下一帧</Button>
                      <Typography.Text type="secondary">
                        {safeSnapshotIndex + 1} / {maxTimelineIndex + 1}
                      </Typography.Text>
                    </Space>
                    <Typography.Text type="secondary">
                      权益: {currentCurvePoint?.equity ?? currentSnapshot?.equity ?? '-'}
                      {' '}| 当日收益率: {currentCurvePoint?.daily_return != null ? fmtPct(currentCurvePoint.daily_return) : '-'}
                    </Typography.Text>
                    {(runFacts?.data_quality?.missing_equity_dates?.length || 0) > 0 || (runFacts?.data_quality?.missing_snapshot_dates?.length || 0) > 0 ? (
                      <Alert
                        type="warning"
                        message="数据对齐存在缺失"
                        description={`缺资金曲线日期: ${(runFacts?.data_quality?.missing_equity_dates || []).join(', ') || '-'}；缺快照日期: ${(runFacts?.data_quality?.missing_snapshot_dates || []).join(', ') || '-'}`}
                      />
                    ) : null}
                    <div className="hidden md:block">
                      <BacktestTable<SnapshotHolding>
                        rowKey={(record, index) => `${record.trade_date || ''}-${record.symbol || ''}-${index}`}
                        columns={holdingColumns}
                        dataSource={currentSnapshotHoldings.length > 0 ? currentSnapshotHoldings : (currentSnapshot?.holdings || [])}
                        pagination={false}
                        scroll={{ x: 760, y: 420 }}
                        locale={{ emptyText: '空仓' }}
                        rowClassName={(record) => (record.position_status === 'closed_today' ? 'opacity-70' : '')}
                      />
                    </div>
                    <div className="md:hidden space-y-2">
                      {(currentSnapshotHoldings.length > 0 ? currentSnapshotHoldings : (currentSnapshot?.holdings || [])).map((h, idx) => (
                        <Card key={`${h.trade_date || ''}-${h.symbol || ''}-${idx}`} size="small" className={h.position_status === 'closed_today' ? 'opacity-70' : ''}>
                          <div className="space-y-1 text-sm">
                            <div className="flex items-center justify-between">
                              <span>{fmtSymbolLabel(h.symbol, h.stock_name)}</span>
                              {h.position_status === 'closed_today' ? <Tag color="gold">当日清仓</Tag> : null}
                            </div>
                            <div>数量：{h.qty ?? '-'}</div>
                            <div>现价：{h.last_price ?? '-'}</div>
                            <div>市值：{h.market_value ?? '-'}</div>
                            <div>权重：{h.weight ?? '-'}</div>
                          </div>
                        </Card>
                      ))}
                      {(currentSnapshotHoldings.length > 0 ? currentSnapshotHoldings : (currentSnapshot?.holdings || [])).length === 0 && (
                        <div className="text-sm text-gray-500">空仓</div>
                      )}
                    </div>
                  </Space>
                </Card>
                </SnapshotErrorBoundary>
              )}
            </Space>
          )}

          {detailTab === 'strategy' && (
            detailLoading.strategy ? (
              <LoadingHint text="策略配置加载中..." />
            ) : (
              <Space direction="vertical" size={12} className="w-full">
                <Descriptions title="业务摘要" bordered size="small" column={2}>
                  <Descriptions.Item label="策略ID">{String(runStrategyConfig?.strategy_id || '-')}</Descriptions.Item>
                  <Descriptions.Item label="基准">{String(runStrategyConfig?.benchmark || '-')}</Descriptions.Item>
                  <Descriptions.Item label="过滤板块">
                    {Array.isArray(filterSummary.boards) ? filterSummary.boards.map((b) => <Tag key={String(b)}>{String(b)}</Tag>) : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="排除ST">{String(filterSummary.exclude_st ?? '-')}</Descriptions.Item>
                  <Descriptions.Item label="候选股票数">{String(filterSummary.candidate_count ?? symbols.count ?? '-')}</Descriptions.Item>
                  <Descriptions.Item label="股票预览">
                    {Array.isArray(symbols.preview) ? `${symbols.preview.slice(0, 8).join(', ')}${symbols.truncated ? ' ...' : ''}` : '-'}
                  </Descriptions.Item>
                </Descriptions>

                <Collapse
                  items={[
                    {
                      key: 'dsl',
                      label: '技术细节（DSL / SQL Preview）',
                      children: (
                        <Space direction="vertical" size={8} className="w-full">
                          <Typography.Text strong>DSL</Typography.Text>
                          <pre className="bg-gray-50 border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(runStrategyConfig?.filter_dsl || {}, null, 2)}</pre>
                          <Typography.Text strong>SQL Preview</Typography.Text>
                          <pre className="bg-gray-50 border rounded-lg p-3 text-xs overflow-auto">{String(runStrategyConfig?.sql_preview || '-')}</pre>
                          <Typography.Text strong>完整配置（raw）</Typography.Text>
                          <pre className="bg-gray-50 border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(runStrategyConfig || {}, null, 2)}</pre>
                        </Space>
                      ),
                    },
                  ]}
                />
              </Space>
            )
          )}
        </Space>
      )}
    </Card>
  )
}
