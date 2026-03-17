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

export function BacktestRecordsPanel({
  runs,
  runsLoading,
  onRefresh,
  onViewDetail,
}: {
  runs: RunItem[]
  runsLoading: boolean
  onRefresh: () => void
  onViewDetail: (runId: number) => void
}) {
  return (
    <div className="bg-white rounded-2xl shadow p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="font-semibold text-gray-800">回测记录</div>
        <button className="text-sm text-indigo-600 hover:underline" onClick={onRefresh}>刷新</button>
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
                  <td><button className="text-indigo-600 hover:underline" onClick={() => onViewDetail(r.id)}>查看详情</button></td>
                </tr>
              ))
            ) : (
              <tr><td className="py-3 text-gray-500" colSpan={9}>暂无回测记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
