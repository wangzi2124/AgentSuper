import type { ChatRequest, ChatResponse } from '../types'
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
