// 用户账号 + JWT 会话管理。
// 后端未配置 AUTH_TOKEN_SECRET：登录关闭，全程匿名（X-User-Id = anonymous）。
// 启用后：需在 /login 页面注册/登录，成功后把 user_id / username / token
// 存入 localStorage，随请求通过 X-User-Id + X-Auth-Token 校验。

import { apiRequest } from './errors'

const STORAGE_KEY = 'agent_super_user_id'
const USERNAME_KEY = 'agent_super_username'
const ACCOUNT_TYPE_KEY = 'agent_super_account_type'
const TOKEN_KEY = 'agent_super_auth_token'
const TOKEN_EXPIRES_KEY = 'agent_super_auth_token_expires_at'

let enabledCache: boolean | null = null
let initPromise: Promise<AuthInitInfo> | null = null

export interface AuthSessionInfo {
  user_id: string
  username: string
  account_type: 'account' | 'device'
}

export interface AuthInitInfo {
  enabled: boolean
  session: AuthSessionInfo | null
}

export function getUserId(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || 'anonymous'
  } catch {
    return 'anonymous'
  }
}

export function setUserId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch { /* noop */ }
}

export function getUsername(): string {
  try {
    return localStorage.getItem(USERNAME_KEY) || getUserId()
  } catch {
    return getUserId()
  }
}

export function setUsername(name: string): void {
  try {
    localStorage.setItem(USERNAME_KEY, name)
  } catch { /* noop */ }
}

function getStoredAccountType(): 'account' | 'device' {
  try {
    return localStorage.getItem(ACCOUNT_TYPE_KEY) === 'account' ? 'account' : 'device'
  } catch {
    return 'device'
  }
}

function setStoredAccountType(t: string): void {
  try {
    localStorage.setItem(ACCOUNT_TYPE_KEY, t)
  } catch { /* noop */ }
}

function storedToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

function storedTokenExpiresAt(): number {
  try {
    return Number(localStorage.getItem(TOKEN_EXPIRES_KEY) || 0)
  } catch {
    return 0
  }
}

function storeToken(token: string, expiresAt: number): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(TOKEN_EXPIRES_KEY, String(expiresAt))
  } catch { /* noop */ }
}

export function getAuthToken(): string {
  return storedToken()
}

export async function isAuthEnabled(): Promise<boolean> {
  if (enabledCache !== null) return enabledCache
  const info = await getAuthInitInfo()
  return info.enabled
}

// 本地是否已持有有效会话（账号登录或历史设备身份）
export function hasStoredSession(): boolean {
  const token = storedToken()
  const exp = storedTokenExpiresAt()
  const now = Math.floor(Date.now() / 1000)
  return getUserId() !== 'anonymous' && !!token && exp > now - 60
}

function storeSession(data: {
  user_id?: string
  username?: string
  token?: string
  expires_at?: number
  account_type?: string
}): void {
  if (data.user_id) setUserId(data.user_id)
  if (data.username) setUsername(data.username)
  if (data.account_type) setStoredAccountType(data.account_type)
  if (data.token && data.expires_at) storeToken(data.token, Number(data.expires_at))
}

function clearSessionLocal(): void {
  setUserId('')
  setUsername('')
  setStoredAccountType('')
  storeToken('', 0)
}

// 启动初始化：探测后端是否启用身份签名；启用时校验并恢复本地会话。
async function doInit(): Promise<AuthInitInfo> {
  let enabled = false
  try {
    const data = await apiRequest<{ enabled: boolean }>('/api/auth/status', { method: 'GET' }, false)
    enabled = !!data?.enabled
  } catch {
    enabled = false
  }
  enabledCache = enabled

  if (!enabled) return { enabled, session: null }
  if (hasStoredSession()) {
    try {
      const me = await fetchMe()
      const session: AuthSessionInfo = {
        user_id: me.user_id,
        username: me.username || me.user_id,
        account_type: me.account_type,
      }
      setUserId(me.user_id)
      setUsername(session.username)
      setStoredAccountType(me.account_type)
      return { enabled, session }
    } catch {
      // token 失效/用户不存在 → 清空本地会话，引导重新登录
      clearSessionLocal()
    }
  }
  return { enabled, session: null }
}

// 兼容旧调用：等待初始化完成（供 addAuthHeaders 等启动竞态使用）
export function ensureAuth(): Promise<void> {
  return getAuthInitInfo().then(() => undefined)
}

// 获取（并缓存）初始化结果；main.ts / 路由守卫 / 请求头共同使用同一 Promise
export function getAuthInitInfo(): Promise<AuthInitInfo> {
  if (!initPromise) initPromise = doInit()
  return initPromise
}

// 账号登录：成功则保存会话并返回
export async function loginAccount(username: string, password: string) {
  const data = await apiRequest<{
    user_id: string
    username: string
    token: string
    expires_at: number
  }>('/api/auth/account/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }, false)
  storeSession({ ...data, account_type: 'account' })
  return data
}

// 账号注册：成功则自动登录（签发 token）并保存会话
export async function registerAccount(username: string, password: string) {
  const data = await apiRequest<{
    user_id: string
    username: string
    token: string
    expires_at: number
  }>('/api/auth/account/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }, false)
  storeSession({ ...data, account_type: 'account' })
  return data
}

// 校验当前本地会话，返回用户信息（token 失效时抛 ApiError）
export async function fetchMe(): Promise<AuthSessionInfo & { created_at: number }> {
  const headers = new Headers()
  headers.set('X-User-Id', getUserId())
  const token = storedToken()
  if (token) headers.set('X-Auth-Token', token)
  return apiRequest('/api/auth/account/me', { method: 'GET', headers }, false)
}

// 退出登录：清空本地会话（不清除服务器数据，下次登录仍可见）
export function logout(): void {
  clearSessionLocal()
}
