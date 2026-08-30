import type { MainTab } from './types'

const mainTabs: { key: MainTab; label: string; hint: string }[] = [
  { key: 'records', label: '回测记录', hint: '任务与结果' },
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
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {mainTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => onChange(tab.key)}
            className={`flex flex-col items-start gap-0.5 rounded-[var(--radius-control)] border px-4 py-3 text-left transition ${
              activeTab === tab.key
                ? 'border-[var(--brand-line)] bg-[var(--brand-soft)]'
                : 'border-[var(--border-subtle)] bg-[var(--surface-subtle)]'
            }`}
          >
            <div className="text-sm font-semibold text-[var(--text-strong)]">{tab.label}</div>
            <div className="text-xs uppercase tracking-[0.16em] text-[var(--text-dim)]">{tab.hint}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
