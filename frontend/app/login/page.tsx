'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { saveAuth } from '../lib/auth'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

export default function LoginPage() {
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [masterKey, setMasterKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!username.trim() || !masterKey.trim()) {
      setError('请填写所有字段')
      return
    }

    try {
      setLoading(true)
      setError('')

      const response = await axios.post(`${API_BASE_URL}/api/auth/register`, {
        username: username.trim(),
        master_key: masterKey.trim()
      })

      if (response.data.success) {
        // 保存认证信息
        saveAuth(response.data.access_token, {
          id: response.data.user_id,
          username: response.data.username,
          is_active: true,
          alert_threshold: 0.7
        })

        // 跳转到首页
        router.push('/')
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '注册失败，请检查主密钥是否正确'
      setError(errorMsg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center py-12 px-4">
      <div className="max-w-md w-full">
        <div className="bg-white rounded-lg shadow-xl p-8">
          {/* Logo and Title */}
          <div className="text-center mb-8">
            <div className="text-5xl mb-4">💰</div>
            <h1 className="text-3xl font-bold text-gray-800 mb-2">
              VeWealth
            </h1>
            <p className="text-gray-600">
              A股股票分析平台
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleRegister} className="space-y-6">
            {/* Username */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                用户名
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="输入用户名（3-50个字符）"
                minLength={3}
                maxLength={50}
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                disabled={loading}
              />
            </div>

            {/* Master Key */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                主密钥
              </label>
              <input
                type="password"
                value={masterKey}
                onChange={(e) => setMasterKey(e.target.value)}
                placeholder="输入主密钥"
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                disabled={loading}
              />
              <p className="mt-1 text-xs text-gray-500">
                需要主密钥才能注册账号，请联系管理员获取
              </p>
            </div>

            {/* Error Message */}
            {error && (
              <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
                <div className="flex items-start gap-2">
                  <span className="text-red-500 text-lg">⚠️</span>
                  <div className="flex-1 text-red-700 text-sm">{error}</div>
                </div>
              </div>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || !username.trim() || !masterKey.trim()}
              className="w-full px-6 py-3 bg-indigo-600 text-white font-medium rounded-lg hover:bg-indigo-700 disabled:bg-gray-400 transition-colors shadow-md"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                  </svg>
                  注册中...
                </span>
              ) : '注册 / 登录'}
            </button>
          </form>

          {/* Info */}
          <div className="mt-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
            <div className="text-sm text-blue-800">
              <p className="font-medium mb-2">💡 使用说明：</p>
              <ul className="list-disc list-inside space-y-1 text-xs">
                <li>首次使用需要主密钥注册账号</li>
                <li>用户名唯一，如已存在会提示错误</li>
                <li>注册成功后自动登录</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

