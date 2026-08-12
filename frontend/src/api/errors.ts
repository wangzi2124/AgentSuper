import { addAuthHeaders } from './fetch'

// 统一 API 错误对象：携带业务错误码、HTTP 状态码与人类可读提示。
export class ApiError extends Error {
  code: number
  status: number
  retryable: boolean

  constructor(code: number, message: string, status: number = 0, retryable: boolean = false) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.retryable = retryable
  }
}

function classifyStatus(status: number): boolean {
  return status === 429 || status >= 500
}

// 解析任意非 2xx 响应为 ApiError（兼容统一 {code,message,data,detail} 与旧 detail/纯文本）。
export async function parseErrorResponse(res: Response): Promise<ApiError> {
  let body: any = null
  try {
    body = await res.json()
  } catch { /* 非 JSON 响应 */ }

  const rawMessage =
    (typeof body?.message === 'string' && body.message) ||
    (typeof body?.detail === 'string' && body.detail) ||
    (typeof body?.detail === 'object' && body.detail?.msg) ||
    (typeof body?.error === 'string' && body.error) ||
    ''
  const message = rawMessage || `请求失败 (${res.status} ${res.statusText || ''})`.trim()
  const code = typeof body?.code === 'number' ? body.code : res.status
  return new ApiError(code, message, res.status, classifyStatus(res.status))
}

// 网络/超时等异常 → ApiError
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  const msg = err instanceof Error ? err.message : String(err)
  const lower = msg.toLowerCase()
  if (lower.includes('abort') || lower.includes('cancelled')) {
    return new ApiError(499, msg, 0, false)
  }
  if (lower.includes('timeout') || lower.includes('timed out')) {
    return new ApiError(408, msg, 408, true)
  }
  if (lower.includes('rate limit') || lower.includes('429')) {
    return new ApiError(429, msg, 429, true)
  }
  if (lower.includes('failed to fetch') || lower.includes('networkerror')) {
    return new ApiError(503, msg, 0, true)
  }
  return new ApiError(1, msg, 0, false)
}

// 统一请求封装：注入认证头，解析统一响应体 {code, message, data}。
// - 成功：返回 data（若响应无统一体则直接返回 body）
// - 失败：抛出 ApiError（message 为后端提示）
// - options.auth=false：不注入认证头（/api/auth/* 等）
export async function apiRequest<T = any>(
  url: string,
  options: RequestInit = {},
  auth: boolean = true,
): Promise<T> {
  let headers: Headers
  try {
    headers = auth ? await addAuthHeaders(options.headers) : new Headers(options.headers)
  } catch {
    headers = new Headers(options.headers)
  }

  let res: Response
  try {
    res = await fetch(url, { ...options, headers })
  } catch (err) {
    throw toApiError(err)
  }

  if (!res.ok) {
    throw await parseErrorResponse(res)
  }

  // SSE / 空响应直接返回
  const ctype = res.headers.get('content-type') || ''
  if (!ctype.includes('application/json')) return res as unknown as T

  const body = await res.json().catch(() => null)
  if (body && typeof body === 'object' && typeof body.code === 'number') {
    if (body.code !== 0) {
      throw new ApiError(body.code, body.message || '请求失败', res.status)
    }
    return body.data as T
  }
  return body as T
}
