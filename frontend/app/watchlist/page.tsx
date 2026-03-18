'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
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
  const [showAddForm, setShowAddForm] = useState(false)
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [alertEnabled, setAlertEnabled] = useState(true)
  const [alertThreshold, setAlertThreshold] = useState<number>(0.7)
  const [addLoading, setAddLoading] = useState(false)
  const [userThreshold, setUserThreshold] = useState(0.7)

  const fetchWatchList = useCallback(async () => {
    try {
      setLoading(true)
      setError('')

      const response = await axios.get(`${API_BASE_URL}/api/watchlist`, {
        headers: getAuthHeader(),
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
    if (!isAuthenticated()) {
      router.push('/login')
      return
    }

    const user = getUser()
    if (user) {
      setUserThreshold(user.alert_threshold)
      setAlertThreshold(user.alert_threshold)
    }

    fetchWatchList()
  }, [fetchWatchList, router])

  const handleAddStock = async () => {
    if (!stockCode.trim()) {
      message.warning('请输入股票代码')
      return
    }

    if (!/^\d{6}$/.test(stockCode.trim())) {
      message.warning('股票代码格式错误，应为6位数字')
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
          alert_threshold: alertThreshold || null,
        },
        {
          headers: getAuthHeader(),
        }
      )

      if (response.data.success) {
        setStockCode('')
        setStockName('')
        setAlertEnabled(true)
        setAlertThreshold(userThreshold || 0.7)
        setShowAddForm(false)
        message.success('添加成功')
        fetchWatchList()
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '添加失败'
      message.error(errorMsg)
    } finally {
      setAddLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await axios.delete(`${API_BASE_URL}/api/watchlist/${id}`, {
        headers: getAuthHeader(),
      })
      message.success('删除成功')
      fetchWatchList()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '删除失败'
      message.error(errorMsg)
    }
  }

  const handleToggleAlert = async (item: WatchListItem) => {
    try {
      await axios.put(
        `${API_BASE_URL}/api/watchlist/${item.id}`,
        {
          alert_enabled: !item.alert_enabled,
        },
        {
          headers: getAuthHeader(),
        }
      )
      fetchWatchList()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '更新失败'
      message.error(errorMsg)
    }
  }

  const columns: ColumnsType<WatchListItem> = [
    { title: '股票代码', dataIndex: 'stock_code', key: 'stock_code', width: 120 },
    {
      title: '股票名称',
      dataIndex: 'stock_name',
      key: 'stock_name',
      width: 140,
      render: (v) => v || '-',
    },
    {
      title: '预警开关',
      dataIndex: 'alert_enabled',
      key: 'alert_enabled',
      width: 120,
      render: (_, row) => (
        <Switch checked={row.alert_enabled} onChange={() => handleToggleAlert(row)} />
      ),
    },
    {
      title: '阈值',
      dataIndex: 'alert_threshold',
      key: 'alert_threshold',
      width: 100,
      align: 'right',
      render: (v) => (v != null ? `${(Number(v) * 100).toFixed(0)}%` : '-'),
    },
    {
      title: '最近预警',
      dataIndex: 'last_alerted_at',
      key: 'last_alerted_at',
      width: 170,
      render: (v) => (v ? new Date(v).toLocaleString() : <Tag>未触发</Tag>),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v) => new Date(v).toLocaleString(),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, row) => (
        <Popconfirm title={`确定要删除 ${row.stock_code} 吗？`} onConfirm={() => handleDelete(row.id)}>
          <Button danger type="link">删除</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 py-8 px-4">
      <div className="max-w-6xl mx-auto space-y-4">
        <Card>
          <div className="flex justify-between items-center">
            <Typography.Title level={3} style={{ margin: 0 }}>👁️ 监控列表</Typography.Title>
            <Button type="primary" onClick={() => setShowAddForm((v) => !v)}>
              {showAddForm ? '取消' : '+ 添加股票'}
            </Button>
          </div>
          <Typography.Text type="secondary">
            默认预警阈值：<Typography.Text strong>{(userThreshold * 100).toFixed(0)}%</Typography.Text>
            <span className="ml-2">（可为每个股票单独设置）</span>
          </Typography.Text>
        </Card>

        {showAddForm && (
          <Card title="添加股票到监控列表">
            <Form layout="vertical" onFinish={handleAddStock}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Form.Item label="股票代码" required>
                  <Input value={stockCode} onChange={(e) => setStockCode(e.target.value)} placeholder="如：000001" maxLength={6} />
                </Form.Item>
                <Form.Item label="股票名称（可选）">
                  <Input value={stockName} onChange={(e) => setStockName(e.target.value)} placeholder="如：平安银行" />
                </Form.Item>
                <Form.Item label="启用预警">
                  <Switch checked={alertEnabled} onChange={setAlertEnabled} />
                </Form.Item>
                <Form.Item label="预警阈值">
                  <InputNumber min={0.1} max={1} step={0.05} precision={2} value={alertThreshold} onChange={(v) => setAlertThreshold(Number(v || 0.7))} />
                </Form.Item>
              </div>
              <Button type="primary" htmlType="submit" loading={addLoading}>添加</Button>
            </Form>
          </Card>
        )}

        {error && <Alert type="error" message={error} />}

        <Card>
          {loading ? (
            <div className="py-16 text-center"><Spin /></div>
          ) : (
            <Table<WatchListItem>
              rowKey="id"
              size="small"
              bordered
              columns={columns}
              dataSource={watchlist}
              pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
              scroll={{ x: 1000 }}
              locale={{ emptyText: '暂无监控股票' }}
            />
          )}
        </Card>
      </div>
    </div>
  )
}
