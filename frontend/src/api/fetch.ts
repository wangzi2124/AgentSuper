import { getUserId } from './auth'

export function addAuthHeaders(headers?: HeadersInit): Headers {
  const h = new Headers(headers || {})
  if (!h.has('X-User-Id')) {
    h.set('X-User-Id', getUserId())
  }
  return h
}

// 带超时功能的 fetch 封装，自动注入 X-User-Id
// timeoutMs 为 0 或未传时表示不启用超时（与既有调用保持一致）
export async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = 0,
): Promise<Response> {
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  if (timeoutMs > 0) {
    controller = new AbortController()
    timer = setTimeout(() => controller!.abort(), timeoutMs)
  }

  // 合并调用方的 signal 与内部超时 signal
  const callerSignal = options.signal
  let signal = controller?.signal
  if (callerSignal && signal) {
    const combined = new AbortController()
    const onAbort = () => combined.abort()
    callerSignal.addEventListener('abort', onAbort, { once: true })
    signal.addEventListener('abort', onAbort, { once: true })
    signal = combined.signal
  } else if (callerSignal) {
    signal = callerSignal
  }

  try {
    return await fetch(url, { ...options, headers: addAuthHeaders(options.headers), signal })
  } finally {
    if (timer) clearTimeout(timer)
  }
}
