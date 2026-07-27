import type { ChatRequest, ChatResponse, SSEEvent } from '../types'
import { fetchWithTimeout } from './fetch'

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
  messages: Array<{ id: string; role: string; content: string; sources?: any[]; steps?: any[] }>
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
  const res = await fetch(BASE + '/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    signal,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Chat stream error: ${err || res.statusText}`)
  }
  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('No response body')
  }
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event: SSEEvent = JSON.parse(line.slice(6))
          onEvent(event)
        } catch { /* skip malformed */ }
      }
    }
  }
}

// 获取所有对话列表
export async function listConversations(): Promise<ConversationMeta[]> {
  const res = await fetchWithTimeout(`${BASE}/conversations`, { method: 'GET' }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`List conversations error: ${err || res.statusText}`)
  }
  return res.json()
}

// 根据 ID 获取对话详情
export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}`, { method: 'GET' }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Get conversation error: ${err || res.statusText}`)
  }
  return res.json()
}

// 重命名对话
export async function renameConversation(conversationId: string, title: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}`, {
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
export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}`, {
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
