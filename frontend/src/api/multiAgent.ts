import type { MultiAgentChatRequest, MultiAgentSSEEvent, ChatError } from '../types'
import { fetchWithTimeout, addAuthHeaders } from './fetch'

const BASE = '/api/chat'

function classifyNetworkError(err: unknown): ChatError {
  const msg = err instanceof Error ? err.message : String(err)
  const lower = msg.toLowerCase()
  if (lower.includes('abort') || lower.includes('aborted')) return { type: 'unknown', message: msg, retryable: false }
  if (lower.includes('timeout') || lower.includes('timed out')) return { type: 'timeout', message: msg, retryable: true }
  if (lower.includes('rate limit') || lower.includes('429')) return { type: 'rate_limit', message: msg, retryable: true }
  if (lower.includes('failed to fetch') || lower.includes('networkerror')) return { type: 'network', message: msg, retryable: true }
  if (lower.includes('500') || lower.includes('502') || lower.includes('503')) return { type: 'server_error', message: msg, retryable: true }
  return { type: 'unknown', message: msg, retryable: false }
}

export async function sendMultiAgentStream(
  data: MultiAgentChatRequest,
  onEvent: (event: MultiAgentSSEEvent) => void,
  signal?: AbortSignal,
  onSessionId?: (sessionId: string) => void,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(BASE + '/multi-agent/stream', {
      method: 'POST',
      headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(data),
      signal,
    })
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
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        if (!receivedTerminalEvent) throw { type: 'network', message: 'Connection interrupted', retryable: true } as ChatError
        break
      }
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
    throw classifyNetworkError(err)
  } finally {
    reader.releaseLock()
  }
}
