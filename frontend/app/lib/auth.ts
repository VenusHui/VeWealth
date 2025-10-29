/**
 * 认证工具函数
 */

const TOKEN_KEY = 'vewealth_token'
const USER_KEY = 'vewealth_user'

export interface UserInfo {
  id: number
  username: string
  wechat_openid?: string
  is_active: boolean
  alert_threshold: number
}

/**
 * 保存认证信息
 */
export function saveAuth(token: string, user: UserInfo) {
  if (typeof window !== 'undefined') {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  }
}

/**
 * 获取Token
 */
export function getToken(): string | null {
  if (typeof window !== 'undefined') {
    return localStorage.getItem(TOKEN_KEY)
  }
  return null
}

/**
 * 获取用户信息
 */
export function getUser(): UserInfo | null {
  if (typeof window !== 'undefined') {
    const userStr = localStorage.getItem(USER_KEY)
    if (userStr) {
      try {
        return JSON.parse(userStr)
      } catch {
        return null
      }
    }
  }
  return null
}

/**
 * 清除认证信息
 */
export function clearAuth() {
  if (typeof window !== 'undefined') {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }
}

/**
 * 检查是否已登录
 */
export function isAuthenticated(): boolean {
  return getToken() !== null
}

/**
 * 获取认证Header
 */
export function getAuthHeader(): Record<string, string> {
  const token = getToken()
  if (token) {
    return {
      'Authorization': `Bearer ${token}`
    }
  }
  return {}
}

