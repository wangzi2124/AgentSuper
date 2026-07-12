import type { ChatRequest, ChatResponse, SSEEvent } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/chat'

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

export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}`, {
    method: 'DELETE',
  }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete conversation error: ${err || res.statusText}`)
  }
}

export async function deleteMessage(conversationId: string, messageId: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/conversations/${conversationId}/messages/${messageId}`, {
    method: 'DELETE',
  }, 0)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete message error: ${err || res.statusText}`)
  }
}
