import type { ChatRequest, ChatResponse, SSEEvent, ChatError } from '../types'
import { fetchWithTimeout, addAuthHeaders } from './fetch'

// 聊天 API 基础路径
const BASE = '/api/chat'

// 对话元信息
export interface ConversationMeta {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// 对话详情，包含完整消息列表
export interface ConversationDetail extends ConversationMeta {
  messages: Array<{ id: string; role: string; content: string; sources?: any[]; steps?: any[]; parts?: any[] }>
}

// 分类前端产生的网络错误
function classifyNetworkError(err: unknown): ChatError {
  const msg = err instanceof Error ? err.message : String(err)
  const lower = msg.toLowerCase()

  if (lower.includes('abort') || lower.includes('aborted')) {
    return { type: 'unknown', message: msg, retryable: false }
  }
  if (lower.includes('timeout') || lower.includes('timed out')) {
    return { type: 'timeout', message: msg, retryable: true }
  }
  if (lower.includes('rate limit') || lower.includes('429') || lower.includes('too many requests')) {
    return { type: 'rate_limit', message: msg, retryable: true }
  }
  if (lower.includes('failed to fetch') || lower.includes('networkerror') || lower.includes('network error')) {
    return { type: 'network', message: msg, retryable: true }
  }
  if (lower.includes('500') || lower.includes('502') || lower.includes('503') || lower.includes('504')) {
    return { type: 'server_error', message: msg, retryable: true }
  }
  return { type: 'unknown', message: msg, retryable: false }
}

// 发送聊天消息（非流式）
export async function sendMessage(data: ChatRequest): Promise<ChatResponse> {
  const res = await fetchWithTimeout(BASE + '/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Chat error: ${err || res.statusText}`)
  }
  return res.json()
}

// 通过 SSE 流式发送聊天消息，逐步回调事件
export async function sendMessageStream(
  data: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(BASE + '/stream', {
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
      message: `Chat stream error: ${err || res.statusText}`,
      retryable: res.status === 429 || res.status >= 500,
      statusCode: res.status,
    }
    throw error
  }
  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('No response body') as any
  }
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedTerminalEvent = false
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        // 流正常结束但没收到 done/error 事件 → 断连
        if (!receivedTerminalEvent) {
          const disconnectError: ChatError = {
            type: 'network',
            message: '连接中断，未收到完整响应',
            retryable: true,
          }
          throw disconnectError
        }
        break
      }
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event: SSEEvent = JSON.parse(line.slice(6))
            if (event.type === 'done' || event.type === 'error') {
              receivedTerminalEvent = true
            }
            onEvent(event)
          } catch (e) {
            if (e instanceof Error && e.message.includes('连接中断')) throw e
            if ((e as ChatError).retryable !== undefined) throw e
            /* skip malformed */
          }
        }
      }
    }
  } catch (err) {
    if (err && typeof err === 'object' && 'retryable' in (err as any)) {
      throw err
    }
    if (signal?.aborted) {
      throw { type: 'unknown', message: 'Cancelled', retryable: false } as ChatError
    }
    throw classifyNetworkError(err)
  } finally {
    reader.releaseLock()
  }
}

function convTypeQuery(conv_type?: string): string {
  return conv_type ? `?conv_type=${conv_type}` : ''
}

// 获取所有对话列表
export async function listConversations(conv_type?: string): Promise<ConversationMeta[]> {
  const res = await fetchWithTimeout(`${BASE}/conversations${convTypeQuery(conv_type)}`, { method: 'GET' }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`List conversations error: ${err || res.statusText}`)
  }
  return res.json()
}

// 根据 ID 获取对话详情
export async function getConversation(conversationId: string, conv_type?: string): Promise<ConversationDetail> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}${convTypeQuery(conv_type)}`, { method: 'GET' }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Get conversation error: ${err || res.statusText}`)
  }
  return res.json()
}

// 重命名对话
export async function renameConversation(conversationId: string, title: string, conv_type?: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}${convTypeQuery(conv_type)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Rename conversation error: ${err || res.statusText}`)
  }
}

// 删除对话
export async function deleteConversation(conversationId: string, conv_type?: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}${convTypeQuery(conv_type)}`, {
    method: 'DELETE',
  }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete conversation error: ${err || res.statusText}`)
  }
}

// 删除对话中的单条消息
export async function deleteMessage(conversationId: string, messageId: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}/messages/${messageId}`, {
    method: 'DELETE',
  }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete message error: ${err || res.statusText}`)
  }
}
