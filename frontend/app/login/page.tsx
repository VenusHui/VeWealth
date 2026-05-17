'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { Alert } from 'antd'
import { saveAuth } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'
import { AppPage } from '../components/ui-shell'

const API_BASE_URL = typeof window !== 'undefined' ? getApiBaseUrl() : 'http://localhost:8001'

export default function LoginPage() {
  const router = useRouter()
  const [isRegisterMode, setIsRegisterMode] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [masterKey, setMasterKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleRegister = async () => {
    if (!username.trim() || !password.trim() || !masterKey.trim()) {
      setError('请填写所有字段')
      return
    }
    if (password.length < 6) {
      setError('密码至少需要 6 个字符')
      return
    }
    if (password.length > 100) {
      setError('密码最多 72 个字符')
      return
    }

    try {
      setLoading(true)
      setError('')
      const response = await axios.post(`${API_BASE_URL}/api/auth/register`, {
        username: username.trim(),
        password,
        master_key: masterKey.trim(),
      })
      if (response.data.success) {
        saveAuth(response.data.access_token, {
          id: response.data.user_id,
          username: response.data.username,
          is_active: true,
          alert_threshold: 0.7,
        })
        router.push('/')
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || '注册失败，请检查信息是否正确')
    } finally {
      setLoading(false)
    }
  }

  const handleLogin = async () => {
    if (!username.trim() || !password.trim()) {
      setError('请填写用户名和密码')
      return
    }
    try {
      setLoading(true)
      setError('')
      const response = await axios.post(`${API_BASE_URL}/api/auth/login`, {
        username: username.trim(),
        password,
      })
      saveAuth(response.data.access_token, {
        id: response.data.user_id,
        username: response.data.username,
        is_active: true,
        alert_threshold: 0.7,
      })
      router.push('/')
    } catch (err: any) {
      setError(err.response?.data?.detail || '登录失败，请检查用户名和密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AppPage className="min-h-[calc(100vh-4.75rem)]">
      <div className="grid flex-1 grid-cols-1 gap-5 lg:grid-cols-[1.2fr_0.9fr]">
        <section className="ve-page-hero justify-between lg:min-h-[680px]">
          <div className="space-y-6">
            <div className="ve-eyebrow">Workspace access</div>
            <div className="space-y-4">
              <h1 className="ve-page-title max-w-2xl">登录 A 股研究席位</h1>
              <p className="ve-page-description max-w-2xl">
                登录后可使用监控列表、策略回测与策略管理；注册需管理员主密钥。
              </p>
            </div>
          </div>
        </section>

        <section className="ve-panel lg:min-h-[680px]">
          <div className="mb-6 flex rounded-full border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.8)] p-1.5">
            <button
              type="button"
              onClick={() => {
                setIsRegisterMode(false)
                setMasterKey('')
                setError('')
              }}
              className="ve-tab-button flex-1 justify-center"
              data-active={!isRegisterMode}
            >
              登录
            </button>
            <button
              type="button"
              onClick={() => {
                setIsRegisterMode(true)
                setError('')
              }}
              className="ve-tab-button flex-1 justify-center"
              data-active={isRegisterMode}
            >
              注册
            </button>
          </div>

          <div className="pb-6">
            <h2 className="text-2xl font-semibold tracking-tight text-[var(--text-strong)]">
              {isRegisterMode ? '注册' : '登录'}
            </h2>
          </div>

          <form
            className="space-y-5"
            onSubmit={(e) => {
              e.preventDefault()
              void (isRegisterMode ? handleRegister() : handleLogin())
            }}
          >
            <div>
              <label htmlFor="username" className="ve-field-label">用户名</label>
              <input
                id="username"
                className="ve-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="输入用户名（3-50 个字符）"
                minLength={3}
                maxLength={50}
                disabled={loading}
              />
            </div>

            <div>
              <label htmlFor="password" className="ve-field-label">密码</label>
              <input
                id="password"
                type="password"
                className="ve-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isRegisterMode ? '设置密码（至少 6 个字符）' : '输入密码'}
                minLength={6}
                maxLength={72}
                disabled={loading}
              />
            </div>

            {isRegisterMode ? (
              <div>
                <label htmlFor="masterKey" className="ve-field-label">主密钥</label>
                <input
                  id="masterKey"
                  type="password"
                  className="ve-input"
                  value={masterKey}
                  onChange={(e) => setMasterKey(e.target.value)}
                  placeholder="输入管理员提供的主密钥"
                  disabled={loading}
                  aria-describedby="master-key-help"
                />
                <p id="master-key-help" className="mt-2 text-xs leading-6 text-[var(--text-dim)]">
                  需要主密钥才能注册账号；如果是私有部署，请仅在可信环境中分发。
                </p>
              </div>
            ) : null}

            {error ? <Alert type="error" showIcon message={error} /> : null}

            <button
              type="submit"
              className="ve-button-primary w-full"
              disabled={
                loading ||
                !username.trim() ||
                !password.trim() ||
                (isRegisterMode && !masterKey.trim())
              }
            >
              {loading ? '处理中…' : isRegisterMode ? '注册' : '登录'}
            </button>
          </form>

        </section>
      </div>
    </AppPage>
  )
}
