'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { Alert, Input, InputNumber, Spin, Tag, message } from 'antd'
import { getAuthHeader, getToken, getUser, isAuthenticated, saveAuth } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'
import { AppPage, PageHeader, SurfaceCard } from '../components/ui-shell'

const API_BASE_URL = getApiBaseUrl()

export default function SettingsPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 微信 OpenID 绑定
  const [openid, setOpenid] = useState('')
  const [boundOpenid, setBoundOpenid] = useState<string | null>(null)
  const [savingOpenid, setSavingOpenid] = useState(false)

  // 默认预警阈值（0-100 百分比展示，0-1 存储）
  const [thresholdPct, setThresholdPct] = useState<number | null>(70)
  const [savingThreshold, setSavingThreshold] = useState(false)

  const applyUser = useCallback((user: { wechat_openid?: string | null; alert_threshold: number }) => {
    const oid = user.wechat_openid || null
    setBoundOpenid(oid)
    setOpenid(oid ?? '')
    const pct = Math.round((user.alert_threshold ?? 0.7) * 100 * 100) / 100
    setThresholdPct(pct)
  }, [])

  // 拉取最新用户信息，并同步到本地缓存
  const fetchMe = useCallback(async () => {
    try {
      const resp = await axios.get(`${API_BASE_URL}/api/auth/me`, {
        headers: getAuthHeader(),
      })
      const me = resp.data
      applyUser(me)
      const token = getToken()
      if (token && me && typeof me.id === 'number') {
        saveAuth(token, me)
      }
    } catch (err: any) {
      if (err.response?.status === 401) {
        router.push('/login')
      } else {
        setError(err.response?.data?.detail || '加载用户信息失败')
      }
    }
  }, [router, applyUser])

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push('/login')
      return
    }
    // 先用本地缓存渲染，再拉取服务端最新数据
    const cached = getUser()
    if (cached) applyUser(cached)
    fetchMe().finally(() => setLoading(false))
  }, [router, fetchMe, applyUser])

  const handleSaveOpenid = async () => {
    const value = openid.trim()
    if (!value) {
      message.warning('请输入微信 OpenID')
      return
    }
    try {
      setSavingOpenid(true)
      setError('')
      const resp = await axios.put(
        `${API_BASE_URL}/api/auth/me`,
        { wechat_openid: value },
        { headers: getAuthHeader() },
      )
      applyUser(resp.data)
      const token = getToken()
      if (token) saveAuth(token, resp.data)
      message.success('微信 OpenID 已保存')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败')
    } finally {
      setSavingOpenid(false)
    }
  }

  const handleSaveThreshold = async () => {
    if (thresholdPct == null || thresholdPct < 0 || thresholdPct > 100) {
      message.warning('预警阈值需在 0-100 之间')
      return
    }
    try {
      setSavingThreshold(true)
      setError('')
      const threshold = Number((thresholdPct / 100).toFixed(4))
      const resp = await axios.put(
        `${API_BASE_URL}/api/auth/me`,
        { alert_threshold: threshold },
        { headers: getAuthHeader() },
      )
      applyUser(resp.data)
      const token = getToken()
      if (token) saveAuth(token, resp.data)
      message.success('默认预警阈值已保存')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败')
    } finally {
      setSavingThreshold(false)
    }
  }

  return (
    <AppPage>
      <PageHeader
        eyebrow="账号设置"
        title="用户设置"
        description="绑定微信 OpenID 与设置默认预警阈值，完成价格预警的微信通知闭环。"
      />

      {loading ? (
        <div className="py-24 text-center">
          <Spin description="加载用户信息中…" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {error ? <Alert className="lg:col-span-2" type="error" showIcon message={error} /> : null}

          {/* 微信 OpenID 绑定 */}
          <SurfaceCard
            title="微信 OpenID 绑定"
            description="预警触发后通过微信发送通知，需要先绑定你的微信 OpenID。"
          >
            <div className="space-y-5">
              <div>
                <div className="mb-2 flex items-center gap-2">
                  <span className="ve-field-label mb-0">当前状态</span>
                  {boundOpenid ? (
                    <Tag color="green">已绑定</Tag>
                  ) : (
                    <Tag>未绑定</Tag>
                  )}
                </div>
                {boundOpenid ? (
                  <p className="text-sm text-[var(--text-muted)]">
                    当前绑定：<span className="font-mono text-[var(--text-strong)]">{boundOpenid}</span>
                  </p>
                ) : (
                  <p className="text-sm text-[var(--text-muted)]">尚未绑定，微信预警暂不可用。</p>
                )}
              </div>

              <div>
                <label htmlFor="wechat-openid" className="ve-field-label">微信 OpenID</label>
                <Input
                  id="wechat-openid"
                  value={openid}
                  onChange={(e) => setOpenid(e.target.value)}
                  placeholder="粘贴你的微信 OpenID"
                  aria-describedby="openid-help"
                />
                <p id="openid-help" className="mt-2 text-xs leading-6 text-[var(--text-dim)]">
                  OpenID 从微信公众平台（用户管理 → 用户列表）获取；如需更换绑定，直接填写新的 OpenID 并保存即可。
                </p>
              </div>

              <button
                type="button"
                className="ve-button-primary w-full"
                onClick={() => void handleSaveOpenid()}
                disabled={savingOpenid || !openid.trim()}
              >
                {savingOpenid ? '保存中…' : boundOpenid ? '更新绑定' : '绑定'}
              </button>
            </div>
          </SurfaceCard>

          {/* 默认预警阈值 */}
          <SurfaceCard
            title="默认预警阈值"
            description="新增自选股预警时默认采用的 GMM 密度阈值，也可在监控台中按个股单独调整。"
          >
            <div className="space-y-5">
              <div>
                <label htmlFor="alert-threshold" className="ve-field-label">默认预警阈值（%）</label>
                <InputNumber
                  id="alert-threshold"
                  className="w-full"
                  min={0}
                  max={100}
                  step={1}
                  precision={2}
                  value={thresholdPct}
                  onChange={(v) => setThresholdPct(v)}
                  addonAfter="%"
                  aria-describedby="threshold-help"
                />
                <p id="threshold-help" className="mt-2 text-xs leading-6 text-[var(--text-dim)]">
                  阈值越高，仅当筹码密集程度超过该值时触发预警。建议从 70% 起步，按自身风险偏好调整。
                </p>
              </div>

              <button
                type="button"
                className="ve-button-primary w-full"
                onClick={() => void handleSaveThreshold()}
                disabled={savingThreshold || thresholdPct == null}
              >
                {savingThreshold ? '保存中…' : '保存阈值'}
              </button>
            </div>
          </SurfaceCard>
        </div>
      )}
    </AppPage>
  )
}
