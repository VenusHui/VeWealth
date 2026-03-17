type MainTab = 'create' | 'records' | 'detail'

const mainTabs: { key: MainTab; label: string }[] = [
  { key: 'create', label: '新建回测' },
  { key: 'records', label: '回测记录' },
  { key: 'detail', label: '回测详情' },
]

export function MainTabSwitcher({
  activeTab,
  onChange,
}: {
  activeTab: MainTab
  onChange: (tab: MainTab) => void
}) {
  return (
    <div className="bg-white rounded-2xl shadow p-2 flex gap-2">
      {mainTabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`px-4 py-2 rounded-xl text-sm font-medium transition ${activeTab === tab.key ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
