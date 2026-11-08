import { fetchWithTimeout } from './fetch'

export interface SlashCommandInfo {
  name: string
  description: string
  agent: string
  model: string
  source: string
}

export async function listCommands(sessionId?: string | null): Promise<SlashCommandInfo[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const res = await fetchWithTimeout(`/api/v2/commands${query}`)
  if (!res.ok) throw new Error(`Failed to list commands: ${res.statusText}`)
  const body = (await res.json()) as { commands?: SlashCommandInfo[] }
  return body.commands ?? []
}
