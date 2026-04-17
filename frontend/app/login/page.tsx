'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { Alert } from 'antd'
import { saveAuth } from '../lib/auth'
import { AppPage, InfoPill } from '../components/ui-shell'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

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
              <h1 className="ve-page-title max-w-2xl">进入你的 A 股研究席位。</h1>
              <p className="ve-page-description max-w-2xl">
                登录后可使用监控列表、策略回测与策略管理详情页；注册仍保留主密钥门槛，用于保护部署环境和共享数据面板。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <InfoPill>JWT 登录</InfoPill>
              <InfoPill>监控与回测权限</InfoPill>
              <InfoPill>主密钥注册门槛</InfoPill>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="ve-metric-card ve-metric-card--brand">
              <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-dim)]">监控</div>
              <div className="text-2xl font-semibold text-[var(--text-strong)]">实时预警</div>
              <p className="text-sm text-[var(--text-muted)]">统一管理股票、开关和阈值，不再散落在多个入口。</p>
            </div>
            <div className="ve-metric-card">
              <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-dim)]">回测</div>
              <div className="text-2xl font-semibold text-[var(--text-strong)]">任务闭环</div>
              <p className="text-sm text-[var(--text-muted)]">从创建任务到查看快照、策略代码，流程更连续。</p>
            </div>
            <div className="ve-metric-card ve-metric-card--warning">
              <div className="text-xs uppercase tracking-[0.18em] text-[var(--text-dim)]">权限</div>
              <div className="text-2xl font-semibold text-[var(--text-strong)]">受控注册</div>
              <p className="text-sm text-[var(--text-muted)]">首次开通仍需要管理员主密钥，适合私有部署场景。</p>
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

          <div className="space-y-2 pb-6">
            <h2 className="text-2xl font-semibold tracking-tight text-[var(--text-strong)]">
              {isRegisterMode ? '创建新的研究席位' : '登录研究席位'}
            </h2>
            <p className="text-sm leading-6 text-[var(--text-muted)]">
              {isRegisterMode
                ? '注册成功后自动登录，并沿用默认预警阈值。'
                : '使用现有账号进入分析、监控和回测工作台。'}
            </p>
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
              {loading ? '处理中…' : isRegisterMode ? '注册并进入工作台' : '登录并进入工作台'}
            </button>
          </form>

          <div className="mt-6 rounded-[24px] border border-[var(--border-subtle)] bg-[rgba(255,255,255,0.72)] p-5">
            <div className="text-sm font-semibold text-[var(--text-strong)]">使用提示</div>
            <ul className="mt-3 space-y-2 text-sm leading-6 text-[var(--text-muted)]">
              {isRegisterMode ? (
                <>
                  <li>• 用户名唯一，建议与研究员身份保持一致。</li>
                  <li>• 密码建议使用强密码，避免与主密钥相同。</li>
                  <li>• 注册成功后会直接落到首页，可继续进入监控与回测。</li>
                </>
              ) : (
                <>
                  <li>• 登录后导航会解锁监控台和回测中心。</li>
                  <li>• 当前登录令牌保存在本地浏览器，适合个人工作站使用。</li>
                  <li>• 如账号失效或忘记密码，请联系管理员处理。</li>
                </>
              )}
            </ul>
          </div>
        </section>
      </div>
    </AppPage>
  )
}
