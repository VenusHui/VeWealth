'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { getAuthHeader, isAuthenticated, getUser } from '../lib/auth'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

interface WatchListItem {
  id: number
  stock_code: string
  stock_name?: string
  alert_enabled: boolean
  alert_threshold?: number
  last_alerted_at?: string
  created_at: string
  updated_at: string
}

export default function WatchListPage() {
  const router = useRouter()
  const [watchlist, setWatchlist] = useState<WatchListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  
  // 添加股票表单
  const [showAddForm, setShowAddForm] = useState(false)
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [alertEnabled, setAlertEnabled] = useState(true)
  const [alertThreshold, setAlertThreshold] = useState<number>(0.7)
  const [addLoading, setAddLoading] = useState(false)
  
  // 用户默认阈值
  const [userThreshold, setUserThreshold] = useState(0.7)

  const fetchWatchList = useCallback(async () => {
    try {
      setLoading(true)
      setError('')
      
      const response = await axios.get(`${API_BASE_URL}/api/watchlist`, {
        headers: getAuthHeader()
      })
      
      if (response.data.success) {
        setWatchlist(response.data.data)
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '获取监控列表失败'
      if (err.response?.status === 401) {
        router.push('/login')
      } else {
        setError(errorMsg)
      }
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => {
    // 检查登录状态
    if (!isAuthenticated()) {
      router.push('/login')
      return
    }
    
    // 获取用户信息
    const user = getUser()
    if (user) {
      setUserThreshold(user.alert_threshold)
    }
    
    fetchWatchList()
  }, [fetchWatchList, router])

  const handleAddStock = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!stockCode.trim()) {
      alert('请输入股票代码')
      return
    }
    
    // 验证股票代码格式
    if (!/^\d{6}$/.test(stockCode.trim())) {
      alert('股票代码格式错误，应为6位数字')
      return
    }
    
    try {
      setAddLoading(true)
      
      const response = await axios.post(
        `${API_BASE_URL}/api/watchlist`,
        {
          stock_code: stockCode.trim(),
          stock_name: stockName.trim() || null,
          alert_enabled: alertEnabled,
          alert_threshold: alertThreshold || null
        },
        {
          headers: getAuthHeader()
        }
      )
      
      if (response.data.success) {
        // 重置表单
        setStockCode('')
        setStockName('')
        setAlertEnabled(true)
        setAlertThreshold(0.7)
        setShowAddForm(false)
        
        // 刷新列表
        fetchWatchList()
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '添加失败'
      alert(errorMsg)
    } finally {
      setAddLoading(false)
    }
  }

  const handleDelete = async (id: number, stockCode: string) => {
    if (!confirm(`确定要删除 ${stockCode} 吗？`)) {
      return
    }
    
    try {
      await axios.delete(`${API_BASE_URL}/api/watchlist/${id}`, {
        headers: getAuthHeader()
      })
      
      // 刷新列表
      fetchWatchList()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '删除失败'
      alert(errorMsg)
    }
  }

  const handleToggleAlert = async (item: WatchListItem) => {
    try {
      await axios.put(
        `${API_BASE_URL}/api/watchlist/${item.id}`,
        {
          alert_enabled: !item.alert_enabled
        },
        {
          headers: getAuthHeader()
        }
      )
      
      // 刷新列表
      fetchWatchList()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '更新失败'
      alert(errorMsg)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center">
        <div className="text-center">
          <div className="text-4xl mb-4">⏳</div>
          <div className="text-gray-600">加载中...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 py-8 px-4">
      <div className="max-w-6xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-4xl font-bold text-gray-800">
            👁️ 监控列表
          </h1>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="px-6 py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition-colors shadow-md"
          >
            {showAddForm ? '取消' : '+ 添加股票'}
          </button>
        </div>

        {/* 用户设置信息 */}
        <div className="mb-6 p-4 bg-white rounded-lg shadow-md">
          <div className="text-sm text-gray-600">
            默认预警阈值：<span className="font-medium text-indigo-600">{(userThreshold * 100).toFixed(0)}%</span>
            <span className="ml-4 text-gray-500">（可为每个股票单独设置）</span>
          </div>
        </div>

        {/* 添加表单 */}
        {showAddForm && (
          <div className="bg-white rounded-lg shadow-lg p-6 mb-6">
            <h2 className="text-xl font-bold text-gray-800 mb-4">添加股票到监控列表</h2>
            <form onSubmit={handleAddStock} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    股票代码 *
                  </label>
                  <input
                    type="text"
                    value={stockCode}
                    onChange={(e) => setStockCode(e.target.value)}
                    placeholder="如：000001"
                    maxLength={6}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    股票名称（可选）
                  </label>
                  <input
                    type="text"
                    value={stockName}
                    onChange={(e) => setStockName(e.target.value)}
                    placeholder="如：平安银行"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id="alertEnabled"
                    checked={alertEnabled}
                    onChange={(e) => setAlertEnabled(e.target.checked)}
                    className="w-5 h-5 text-indigo-600"
                  />
                  <label htmlFor="alertEnabled" className="text-sm font-medium text-gray-700">
                    启用价格预警
                  </label>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    预警阈值（留空使用默认值）
                  </label>
                  <input
                    type="number"
                    value={alertThreshold}
                    onChange={(e) => setAlertThreshold(parseFloat(e.target.value))}
                    min={0}
                    max={1}
                    step={0.01}
                    placeholder={`默认：${userThreshold}`}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>
              
              <button
                type="submit"
                disabled={addLoading}
                className="w-full px-6 py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:bg-gray-400 transition-colors"
              >
                {addLoading ? '添加中...' : '添加到监控列表'}
              </button>
            </form>
          </div>
        )}

        {/* 错误信息 */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg">
            <div className="flex items-start gap-2">
              <span className="text-red-500 text-lg">⚠️</span>
              <div className="flex-1 text-red-700">{error}</div>
            </div>
          </div>
        )}

        {/* 监控列表 */}
        {watchlist.length === 0 ? (
          <div className="bg-white rounded-lg shadow-lg p-12 text-center">
            <div className="text-6xl mb-4">📭</div>
            <div className="text-xl text-gray-600 mb-2">监控列表为空</div>
            <div className="text-gray-500 mb-6">点击上方按钮添加关注的股票</div>
            <button
              onClick={() => setShowAddForm(true)}
              className="px-6 py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 transition-colors"
            >
              + 添加第一只股票
            </button>
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow-lg overflow-hidden">
            <table className="w-full">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-700">股票代码</th>
                  <th className="px-6 py-4 text-left text-sm font-medium text-gray-700">股票名称</th>
                  <th className="px-6 py-4 text-center text-sm font-medium text-gray-700">预警状态</th>
                  <th className="px-6 py-4 text-center text-sm font-medium text-gray-700">预警阈值</th>
                  <th className="px-6 py-4 text-center text-sm font-medium text-gray-700">最后预警</th>
                  <th className="px-6 py-4 text-center text-sm font-medium text-gray-700">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {watchlist.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 font-medium text-gray-800">{item.stock_code}</td>
                    <td className="px-6 py-4 text-gray-600">{item.stock_name || '-'}</td>
                    <td className="px-6 py-4 text-center">
                      <button
                        onClick={() => handleToggleAlert(item)}
                        className={`px-3 py-1 rounded-full text-xs font-medium ${
                          item.alert_enabled
                            ? 'bg-green-100 text-green-700'
                            : 'bg-gray-100 text-gray-600'
                        }`}
                      >
                        {item.alert_enabled ? '✓ 已启用' : '✕ 已禁用'}
                      </button>
                    </td>
                    <td className="px-6 py-4 text-center text-gray-600">
                      {item.alert_threshold 
                        ? `${(item.alert_threshold * 100).toFixed(0)}%`
                        : `${(userThreshold * 100).toFixed(0)}% (默认)`
                      }
                    </td>
                    <td className="px-6 py-4 text-center text-sm text-gray-500">
                      {item.last_alerted_at 
                        ? new Date(item.last_alerted_at).toLocaleString('zh-CN')
                        : '从未'
                      }
                    </td>
                    <td className="px-6 py-4 text-center">
                      <button
                        onClick={() => handleDelete(item.id, item.stock_code)}
                        className="text-red-600 hover:text-red-800 font-medium"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 提示信息 */}
        <div className="mt-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
          <div className="text-sm text-blue-800">
            <p className="font-medium mb-2">💡 功能说明：</p>
            <ul className="list-disc list-inside space-y-1 text-xs">
              <li>系统会每个交易日自动采集监控列表中股票的分时数据</li>
              <li>交易时间内每5分钟检查一次价格预警</li>
              <li>当价格超过正态分布阈值时，会通过微信公众号通知（需绑定微信）</li>
              <li>同一股票1小时内不会重复发送预警</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}

