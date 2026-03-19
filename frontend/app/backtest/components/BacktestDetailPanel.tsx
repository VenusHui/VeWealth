import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
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
} from 'recharts'
import { LoadingHint } from './LoadingHint'
import { BacktestTable } from './BacktestTable'
import type {
  BacktestOverview,
  DetailTab,
  RoundRow,
  SnapshotHolding,
  SnapshotRow,
  StrategyConfig,
  TradeRow,
} from './types'

const detailTabs: { key: DetailTab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'trades', label: '成交明细' },
  { key: 'rounds', label: '回合交易' },
  { key: 'snapshots', label: '持仓快照' },
  { key: 'strategy', label: '策略配置' },
]

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
  const [snapshotIndex, setSnapshotIndex] = useState(0)

  useEffect(() => {
    if (snapshotItems.length === 0) {
      setSnapshotIndex(0)
      return
    }
    setSnapshotIndex(snapshotItems.length - 1)
  }, [snapshotItems.length])

  const currentSnapshot = snapshotItems[snapshotIndex]

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
              </Space>
            )
          )}

          {detailTab === 'rounds' && (
            detailLoading.rounds ? (
              <LoadingHint text="回合交易加载中..." />
            ) : (
              <Space direction="vertical" size={12} className="w-full">
                <Button onClick={() => onDownloadCsv(`${apiBaseUrl}/api/backtest/runs/${selectedRunId}/rounds/export`, `backtest_run_${selectedRunId}_rounds.csv`)}>导出回合 CSV</Button>
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
              </Space>
            )
          )}

          {detailTab === 'snapshots' && (
            <Space direction="vertical" size={12} className="w-full">
              {detailLoading.snapshots ? (
                <LoadingHint text="持仓快照加载中..." />
              ) : snapshotItems.length === 0 ? (
                <Empty description="暂无持仓快照数据" />
              ) : (
                <Card size="small" title={`快照时间：${currentSnapshot?.snapshot_time || '-'}`}>
                  <Space direction="vertical" size={12} className="w-full">
                    <Slider
                      min={0}
                      max={Math.max(snapshotItems.length - 1, 0)}
                      value={snapshotIndex}
                      onChange={(value) => setSnapshotIndex(Number(value))}
                      tooltip={{ formatter: (value) => snapshotItems[Number(value || 0)]?.snapshot_time || '' }}
                    />
                    <Typography.Text type="secondary">
                      权益: {currentSnapshot?.equity} | 现金: {currentSnapshot?.cash} | 持仓市值: {currentSnapshot?.position_value}
                    </Typography.Text>
                    <BacktestTable<SnapshotHolding>
                      rowKey={(record, index) => `${record.symbol || ''}-${index}`}
                      columns={holdingColumns}
                      dataSource={currentSnapshot?.holdings || []}
                      pagination={false}
                      scroll={{ x: 680, y: 420 }}
                      locale={{ emptyText: '空仓' }}
                    />
                  </Space>
                </Card>
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
