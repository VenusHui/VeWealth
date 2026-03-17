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
import type {
  BacktestOverview,
  DetailTab,
  RoundRow,
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
}) {
  return (
    <div className="bg-white rounded-2xl shadow p-5 space-y-4">
      <div className="font-semibold text-gray-800">回测详情 {selectedRunId ? `(Run #${selectedRunId})` : ''}</div>
      {!selectedRunId ? (
        <div className="text-sm text-gray-500">请先在「回测记录」中选择一条记录</div>
      ) : (
        <>
          <div className="flex gap-2 flex-wrap">
            {detailTabs.map((tab) => (
              <button key={tab.key} onClick={() => onChangeDetailTab(tab.key)} className={`px-3 py-1 rounded-lg text-sm ${detailTab === tab.key ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
                {tab.label}
              </button>
            ))}
          </div>

          {detailTab === 'overview' && (
            <div className="space-y-4">
              {detailLoading.overview ? (
                <LoadingHint text="概览数据加载中..." />
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
                <LoadingHint text="成交明细加载中..." />
              ) : (
                <>
                  <button className="inline-block px-3 py-1 text-xs rounded bg-indigo-50 text-indigo-700" onClick={() => onDownloadCsv(`${apiBaseUrl}/api/backtest/runs/${selectedRunId}/trades/export`, `backtest_run_${selectedRunId}_trades.csv`)}>导出成交 CSV</button>
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
                <LoadingHint text="回合交易加载中..." />
              ) : (
                <>
                  <button className="inline-block px-3 py-1 text-xs rounded bg-indigo-50 text-indigo-700" onClick={() => onDownloadCsv(`${apiBaseUrl}/api/backtest/runs/${selectedRunId}/rounds/export`, `backtest_run_${selectedRunId}_rounds.csv`)}>导出回合 CSV</button>
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
                <LoadingHint text="持仓快照加载中..." />
              ) : runSnapshots.length === 0 ? (
                <div className="text-gray-500">暂无持仓快照数据</div>
              ) : (
                runSnapshots.slice(-20).reverse().map((s, i) => (
                  <div key={i} className="border rounded-lg p-3 bg-gray-50">
                    <div className="font-medium">{s.snapshot_time}</div>
                    <div className="text-xs text-gray-600 mt-1">权益: {s.equity} | 现金: {s.cash} | 持仓市值: {s.position_value}</div>
                    <div className="overflow-auto mt-2"><table className="w-full text-xs"><thead><tr className="border-b"><th className="text-left py-1">标的</th><th>数量</th><th>现价</th><th>市值</th><th>权重</th></tr></thead><tbody>{(s.holdings || []).map((h, hi: number) => <tr key={hi} className="border-b"><td className="py-1">{h.symbol}</td><td>{h.qty}</td><td>{h.last_price}</td><td>{h.market_value}</td><td>{h.weight}</td></tr>)}{(s.holdings || []).length === 0 && <tr><td colSpan={5} className="py-1 text-gray-500">空仓</td></tr>}</tbody></table></div>
                  </div>
                ))
              )}
            </div>
          )}

          {detailTab === 'strategy' && (
            detailLoading.strategy ? (
              <LoadingHint text="策略配置加载中..." />
            ) : (
              <pre className="bg-gray-50 border rounded-lg p-3 text-xs overflow-auto">{JSON.stringify(runStrategyConfig || {}, null, 2)}</pre>
            )
          )}
        </>
      )}
    </div>
  )
}
