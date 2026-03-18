'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Segmented,
  Space,
  Typography,
} from 'antd'
import { saveAuth } from '../lib/auth'

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
      setError('密码至少需要6个字符')
      return
    }

    if (password.length > 100) {
      setError('密码最多72个字符')
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
      const errorMsg = err.response?.data?.detail || '注册失败，请检查信息是否正确'
      setError(errorMsg)
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
      const errorMsg = err.response?.data?.detail || '登录失败，请检查用户名和密码'
      setError(errorMsg)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = isRegisterMode ? handleRegister : handleLogin

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center py-12 px-4">
      <div className="max-w-md w-full">
        <Card>
          <div className="text-center mb-6">
            <div className="text-5xl mb-3">💰</div>
            <Typography.Title level={2} style={{ margin: 0 }}>
              VeWealth
            </Typography.Title>
            <Typography.Text type="secondary">A股股票分析平台</Typography.Text>
          </div>

          <div className="mb-6 flex justify-center">
            <Segmented
              value={isRegisterMode ? 'register' : 'login'}
              options={[
                { label: '登录', value: 'login' },
                { label: '注册', value: 'register' },
              ]}
              onChange={(v) => {
                const register = v === 'register'
                setIsRegisterMode(register)
                setError('')
                if (!register) setMasterKey('')
              }}
            />
          </div>

          <Form layout="vertical" onFinish={handleSubmit}>
            <Form.Item label="用户名" required>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="输入用户名（3-50个字符）"
                minLength={3}
                maxLength={50}
                disabled={loading}
              />
            </Form.Item>

            <Form.Item label="密码" required>
              <Input.Password
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isRegisterMode ? '设置密码（最少6个字符）' : '输入密码'}
                minLength={6}
                maxLength={72}
                disabled={loading}
              />
            </Form.Item>

            {isRegisterMode && (
              <Form.Item label="主密钥" required>
                <Input.Password
                  value={masterKey}
                  onChange={(e) => setMasterKey(e.target.value)}
                  placeholder="输入管理员提供的主密钥"
                  disabled={loading}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  需要主密钥才能注册账号，请联系管理员获取
                </Typography.Text>
              </Form.Item>
            )}

            {error && <Alert style={{ marginBottom: 16 }} type="error" showIcon message={error} />}

            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
              disabled={
                loading ||
                !username.trim() ||
                !password.trim() ||
                (isRegisterMode && !masterKey.trim())
              }
            >
              {isRegisterMode ? '注册账号' : '登录'}
            </Button>
          </Form>

          <Card size="small" style={{ marginTop: 16 }}>
            <Typography.Text strong>💡 使用说明：</Typography.Text>
            <Space direction="vertical" size={2} style={{ marginTop: 8 }}>
              {isRegisterMode ? (
                <>
                  <Typography.Text style={{ fontSize: 12 }}>• 首次使用需要管理员提供主密钥注册账号</Typography.Text>
                  <Typography.Text style={{ fontSize: 12 }}>• 用户名唯一，3-50个字符</Typography.Text>
                  <Typography.Text style={{ fontSize: 12 }}>• 密码至少6个字符，建议使用强密码</Typography.Text>
                  <Typography.Text style={{ fontSize: 12 }}>• 注册成功后自动登录</Typography.Text>
                </>
              ) : (
                <>
                  <Typography.Text style={{ fontSize: 12 }}>• 使用注册时的用户名和密码登录</Typography.Text>
                  <Typography.Text style={{ fontSize: 12 }}>• 登录令牌有效期为1天</Typography.Text>
                  <Typography.Text style={{ fontSize: 12 }}>• 如忘记密码请联系管理员</Typography.Text>
                </>
              )}
            </Space>
          </Card>
        </Card>
      </div>
    </div>
  )
}
