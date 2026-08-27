import { addAuthHeaders } from './fetch'
import type { ChatError } from '../types'

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

// [S3] 单一聊天错误分类实现：收敛自 store 与 api/multiAgent.ts 的两份重复。
// 所有聊天链路的网络异常都归一到这里，避免三处逻辑漂移。
export function classifyNetworkError(err: unknown): ChatError {
  const msg = err instanceof Error ? err.message : String(err)
  const lower = msg.toLowerCase()
  // 超时/stall 相关 abort 优先判为可重试（放在通用 abort 检查之前）
  if (lower.includes('timeout') || lower.includes('timed out') || lower.includes('stall'))
    return { type: 'timeout', message: msg, retryable: true }
  if (lower.includes('abort') || lower.includes('aborted'))
    return { type: 'unknown', message: msg, retryable: false }
  if (lower.includes('rate limit') || lower.includes('429') || lower.includes('too many requests'))
    return { type: 'rate_limit', message: msg, retryable: true }
  if (lower.includes('failed to fetch') || lower.includes('networkerror'))
    return { type: 'network', message: msg, retryable: true }
  if (lower.includes('500') || lower.includes('502') || lower.includes('503') || lower.includes('504'))
    return { type: 'server_error', message: msg, retryable: true }
  return { type: 'unknown', message: msg, retryable: false }
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
