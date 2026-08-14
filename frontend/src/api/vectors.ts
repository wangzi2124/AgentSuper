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

export interface VectorStoreConfig {
  auto_clear: boolean
  ttl_days: number
  cleanup_interval_hours: number
  count: number
}

export interface ClearResult {
  message: string
  removed_vectors: number
  removed_chapters: number
  removed_documents: number
}

// 获取向量库清理相关配置
export async function getVectorConfig(): Promise<VectorStoreConfig> {
  const res = await fetchWithTimeout(BASE + '/config')
  if (!res.ok) throw new Error(`Failed to get vector config: ${res.statusText}`)
  return res.json()
}

// 清空全部知识库数据（向量库 + 章节库 + BM25 + 上传文件）
export async function clearVectors(): Promise<ClearResult> {
  const res = await fetchWithTimeout(BASE + '/clear', { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to clear vector store: ${res.statusText}`)
  return res.json()
}

// 按 TTL 配置手动清理过期文档
export async function clearExpiredVectors(): Promise<{ message: string; removed: number; ttl_days: number }> {
  const res = await fetchWithTimeout(BASE + '/clear-expired', { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to clear expired: ${res.statusText}`)
  return res.json()
}
