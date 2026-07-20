import type { ChunkListResponse } from '../types'
import { fetchWithTimeout } from './fetch'

// 向量 API 基础路径
const BASE = '/api/vectors'

// 分页获取向量分块列表，支持按文档ID和查询词过滤
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
