import { fetchWithTimeout } from './fetch'

export interface ReferenceItem {
  name: string
  kind: string
  description: string
  hidden: boolean
  status: string
  error: string
  root: string
}

export async function listReferences(sessionId?: string | null): Promise<ReferenceItem[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const res = await fetchWithTimeout(`/api/v2/references${query}`)
  if (!res.ok) throw new Error(`Failed to list references: ${res.statusText}`)
  const body = (await res.json()) as { references?: ReferenceItem[] }
  return body.references ?? []
}
