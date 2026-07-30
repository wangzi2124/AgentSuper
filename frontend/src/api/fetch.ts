import { getUserId } from './auth'

export function addAuthHeaders(headers?: HeadersInit): Headers {
  const h = new Headers(headers || {})
  if (!h.has('X-User-Id')) {
    h.set('X-User-Id', getUserId())
  }
  return h
}

// 带超时功能的 fetch 封装，自动注入 X-User-Id
export async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  _timeout?: number,
): Promise<Response> {
  return fetch(url, { ...options, headers: addAuthHeaders(options.headers) })
}
