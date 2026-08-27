import type { MultiAgentChatRequest, MultiAgentSSEEvent, ChatError } from '../types'
import { classifyNetworkError } from './errors'
import { fetchWithTimeout, addAuthHeaders } from './fetch'

const BASE = '/api/chat'

export async function sendMultiAgentStream(
  data: MultiAgentChatRequest,
  onEvent: (event: MultiAgentSSEEvent) => void,
  signal?: AbortSignal,
  onSessionId?: (sessionId: string) => void,
): Promise<void> {
  let res: Response
  try {
    // S1: 连接阶段使用 fetchWithTimeout（30s），防止服务端挂起不响应
    res = await fetchWithTimeout(BASE + '/multi-agent/stream', {
      method: 'POST',
      headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(data),
      signal,
    }, 30_000)
  } catch (err) {
    throw classifyNetworkError(err)
  }
  if (!res.ok) {
    const err = await res.text()
    const error: ChatError = {
      type: res.status === 429 ? 'rate_limit' : res.status >= 500 ? 'server_error' : 'unknown',
      message: `Stream error: ${err || res.statusText}`,
      retryable: res.status === 429 || res.status >= 500,
      statusCode: res.status,
    }
    throw error
  }
  // 响应头尽早透出会话 id：后端在返回流式响应的同时注入 X-Session-Id，
  // 前端在消费任何 SSE 事件前即可记录 conversation_id，保证"停止/撤销"能立刻打断后台任务
  const sessionId = res.headers.get('X-Session-Id')
  if (sessionId && onSessionId) {
    onSessionId(sessionId)
  }
  const reader = res.body?.getReader()
  if (!reader) throw { type: 'network', message: 'No response body', retryable: true } as ChatError
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedTerminalEvent = false
  // S1: stall 检测 — 记录最近事件时间戳，>60s 无事件 → abort + 按可重试错误处理
  const STALL_TIMEOUT_MS = 60_000
  let lastEventTime = Date.now()
  let stallTimer: ReturnType<typeof setTimeout> | null = null
  let stallTriggered = false
  const stallCheck = () => {
    const elapsed = Date.now() - lastEventTime
    if (elapsed > STALL_TIMEOUT_MS && !stallTriggered) {
      stallTriggered = true
      reader.cancel(new DOMException('stall timeout', 'TimeoutError')).catch(() => {})
      return
    }
    if (!stallTriggered) {
      stallTimer = setTimeout(stallCheck, Math.min(10_000, STALL_TIMEOUT_MS - elapsed))
    }
  }
  stallTimer = setTimeout(stallCheck, 10_000)
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        if (!receivedTerminalEvent) throw { type: 'network', message: 'Connection interrupted', retryable: true } as ChatError
        break
      }
      lastEventTime = Date.now()
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event: MultiAgentSSEEvent = JSON.parse(line.slice(6))
            if (event.type === 'done' || event.type === 'error') receivedTerminalEvent = true
            onEvent(event)
          } catch (e) {
            if (e && typeof e === 'object' && 'retryable' in (e as any)) throw e
          }
        }
      }
    }
  } catch (err) {
    if (err && typeof err === 'object' && 'retryable' in (err as any)) throw err
    if (signal?.aborted) throw { type: 'unknown', message: 'Cancelled', retryable: false } as ChatError
    // S1: stall 超时归类为可重试的 timeout 错误
    if (stallTriggered) {
      throw { type: 'timeout', message: '连接超时，正在重试', retryable: true } as ChatError
    }
    throw classifyNetworkError(err)
  } finally {
    if (stallTimer) clearTimeout(stallTimer)
    reader.releaseLock()
  }
}
