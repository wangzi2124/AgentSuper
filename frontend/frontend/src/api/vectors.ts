import type { ChunkListResponse } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/vectors'

export async function listChunks(
  offset = 0, limit = 50, documentId?: string, query?: string,
): Promise<ChunkListResponse> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (documentId) params.set('document_id', documentId)
  if (query) params.set('query', query)
  const res = await fetchWithTimeout(BASE + '/?' + params.toString())
  if (!res.ok) throw new Error(`Failed to list chunks: ${res.statusText}`)
  return res.json()
}
