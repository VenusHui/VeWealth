import type { MainTab } from './types'

const mainTabs: { key: MainTab; label: string; hint: string }[] = [
  { key: 'create', label: '新建任务', hint: '配置与提交' },
  { key: 'records', label: '回测记录', hint: '历史运行' },
  { key: 'detail', label: '结果详情', hint: '成交与快照' },
  { key: 'strategies', label: '策略管理', hint: '代码与状态' },
]

export function MainTabSwitcher({
  activeTab,
  onChange,
}: {
  activeTab: MainTab
  onChange: (tab: MainTab) => void
}) {
  return (
    <div className="ve-panel p-2.5">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {mainTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => onChange(tab.key)}
            className="rounded-[20px] border px-4 py-3 text-left transition"
            style={{
              background:
                activeTab === tab.key
                  ? 'linear-gradient(135deg, rgba(15,118,110,0.14), rgba(14,116,144,0.08))'
                  : 'rgba(255,255,255,0.55)',
              borderColor:
                activeTab === tab.key ? 'rgba(15,118,110,0.18)' : 'rgba(15,23,42,0.06)',
            }}
          >
            <div className="text-sm font-semibold text-[var(--text-strong)]">{tab.label}</div>
            <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-dim)]">{tab.hint}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
