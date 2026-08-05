// 用户身份 + 签名 token 管理。
// 默认（后端未配置 AUTH_TOKEN_SECRET）：仅保留原 X-User-Id 行为。
// 启用后：前端生成随机 user_id + device_secret → /api/auth/register 首次绑定 →
//         经 /api/auth/token 换取签名 token，随请求携带 X-Auth-Token。

const STORAGE_KEY = 'agent_super_user_id'
const SECRET_KEY = 'agent_super_device_secret'
const TOKEN_KEY = 'agent_super_auth_token'
const TOKEN_EXPIRES_KEY = 'agent_super_auth_token_expires_at'

let enabledCache: boolean | null = null

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

function randomId(): string {
  // 浏览器安全随机串（UUID v4）
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `u-${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function getDeviceSecret(): string {
  try {
    let s = localStorage.getItem(SECRET_KEY)
    if (!s) {
      const bytes = new Uint8Array(32)
      crypto.getRandomValues(bytes)
      s = btoa(String.fromCharCode(...bytes)).replace(/[^a-zA-Z0-9]/g, '')
      localStorage.setItem(SECRET_KEY, s)
    }
    return s
  } catch {
    return 'no-crypto'
  }
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

let authReady: Promise<void> | null = null

async function doEnsureAuth(): Promise<void> {
  if (!(await isAuthEnabled())) return

  let uid = getUserId()
  if (uid === 'anonymous' || !uid) {
    uid = randomId()
    setUserId(uid)
  }
  const secret = getDeviceSecret()

  // 首次绑定
  try {
    const reg = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid, device_secret: secret }),
    })
    if (!reg.ok && reg.status !== 409) {
      console.warn('[auth] register failed', reg.status)
    }
  } catch (e) {
    console.warn('[auth] register error', e)
  }

  // 签发/续期 token
  const now = Math.floor(Date.now() / 1000)
  if (storedToken() && storedTokenExpiresAt() > now + 60) return
  try {
    const tok = await fetch('/api/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid, device_secret: secret }),
    })
    if (!tok.ok) {
      console.warn('[auth] token issue failed', tok.status)
      return
    }
    const data = await tok.json()
    storeToken(data.token, Number(data.expires_at) || now + 2592000)
  } catch (e) {
    console.warn('[auth] token error', e)
  }
}

export function ensureAuth(): Promise<void> {
  if (!authReady) authReady = doEnsureAuth()
  return authReady
}

export function getAuthToken(): string {
  return storedToken()
}

// 测试/调试用：重置内存缓存与已存密钥
export function resetAuthCache(): void {
  enabledCache = null
  authReady = null
}
