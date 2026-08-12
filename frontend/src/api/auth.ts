// 用户账号 + 签名 token 管理。
// 后端未配置 AUTH_TOKEN_SECRET：登录关闭，全程匿名（X-User-Id = anonymous）。
// 启用后：需在 /login 页面注册/登录，成功后把 user_id / username / token
// 存入 localStorage，随请求通过 X-User-Id + X-Auth-Token 校验。

const STORAGE_KEY = 'agent_super_user_id'
const USERNAME_KEY = 'agent_super_username'
const TOKEN_KEY = 'agent_super_auth_token'
const TOKEN_EXPIRES_KEY = 'agent_super_auth_token_expires_at'

let enabledCache: boolean | null = null

export interface AuthSession {
  user_id: string
  username: string
  token: string
  expires_at: number
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
  try {
    const res = await fetch('/api/auth/status')
    if (!res.ok) {
      enabledCache = false
      return false
    }
    const data = await res.json()
    enabledCache = Boolean(data.enabled)
  } catch {
    enabledCache = false
  }
  return enabledCache
}

// 本地是否已持有有效会话（账号登录或历史设备身份）
export function hasStoredSession(): boolean {
  const token = storedToken()
  const exp = storedTokenExpiresAt()
  const now = Math.floor(Date.now() / 1000)
  return getUserId() !== 'anonymous' && !!token && exp > now - 60
}

function storeSession(data: Partial<AuthSession>): void {
  if (data.user_id) setUserId(data.user_id)
  if (data.username) setUsername(data.username)
  if (data.token && data.expires_at) storeToken(data.token, Number(data.expires_at))
}

function clearSessionLocal(): void {
  setUserId('')
  setUsername('')
  storeToken('', 0)
}

function postJson(url: string, body: Record<string, unknown>): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    if (typeof data?.detail === 'object' && data.detail?.msg) return data.detail.msg
  } catch { /* fallthrough */ }
  return `请求失败 (${res.status})`
}

// 账号登录：成功则保存会话并返回
export async function loginAccount(username: string, password: string): Promise<AuthSession> {
  const res = await postJson('/api/auth/account/login', { username, password })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(await parseError(res))
  storeSession(data)
  return data as AuthSession
}

// 账号注册：成功则自动登录（签发 token）并保存会话
export async function registerAccount(username: string, password: string): Promise<AuthSession> {
  const res = await postJson('/api/auth/account/register', { username, password })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(await parseError(res))
  storeSession(data)
  return data as AuthSession
}

// 校验当前本地会话，返回用户信息（token 失效时抛错）
export async function fetchMe(): Promise<{
  user_id: string
  username: string
  account_type: 'account' | 'device'
  created_at: number
}> {
  const headers = new Headers()
  headers.set('X-User-Id', getUserId())
  const token = storedToken()
  if (token) headers.set('X-Auth-Token', token)
  const res = await fetch('/api/auth/account/me', { headers })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.detail || 'Unauthorized')
  return data
}

// 退出登录：清空本地会话（不清除服务器数据，下次登录仍可见）
export function logout(): void {
  clearSessionLocal()
}

// 兼容旧调用：登录初始化由 auth store 在 main.ts 完成，此处不阻塞请求头构造
export function ensureAuth(): Promise<void> {
  return Promise.resolve()
}
