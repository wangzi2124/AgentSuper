import { apiRequest } from './errors'
import type { ModelInfo } from './models'

export interface LocalModelSources {
  endpoints: Record<string, string>
  priority: string[]
}

export interface HubModel {
  id: string
  source: string
  name: string
  downloads: number
  tags: string[]
}

export interface HubFile {
  file: string
  files: string[]
  size: number
  split: boolean
  parts?: number
  quantization: string
  source: string
  repo_id: string
}

export type LocalDownloadState =
  | 'pending'
  | 'downloading'
  | 'importing'
  | 'done'
  | 'error'
  | 'cancelled'

export interface LocalDownloadJob {
  id: string
  state: LocalDownloadState
  repo_id: string
  file: string
  files: string[]
  source: string
  name?: string
  quantization?: string
  total: number
  downloaded: number
  percent: number
  speed: number
  error: string
  entry_id: string
  entry?: ModelInfo
  created_at: number
  updated_at: number
}

export interface LocalDownloadInput {
  repo_id: string
  file: string
  files?: string[]
  size?: number
  source?: string
  name?: string
  quantization?: string
  context_length?: number
}

export async function fetchLocalModelSources(): Promise<LocalModelSources> {
  return apiRequest<LocalModelSources>('/api/local-models/sources', { method: 'GET' }, true)
}

export async function searchHubModels(q: string, source = 'auto', limit = 20): Promise<HubModel[]> {
  const params = new URLSearchParams({ q, source, limit: String(limit) })
  const data = await apiRequest<{ models: HubModel[] }>(
    `/api/local-models/search?${params.toString()}`,
    { method: 'GET' },
    true,
  )
  return data.models || []
}

export async function fetchHubFiles(repoId: string, source = 'auto'): Promise<HubFile[]> {
  const params = new URLSearchParams({ repo_id: repoId, source })
  const data = await apiRequest<{ files: HubFile[] }>(
    `/api/local-models/files?${params.toString()}`,
    { method: 'GET' },
    true,
  )
  return data.files || []
}

export async function createLocalDownload(input: LocalDownloadInput): Promise<LocalDownloadJob> {
  const data = await apiRequest<{ job: LocalDownloadJob }>('/api/local-models/downloads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }, true)
  return data.job
}

export async function fetchLocalDownloads(): Promise<LocalDownloadJob[]> {
  const data = await apiRequest<{ jobs: LocalDownloadJob[] }>(
    '/api/local-models/downloads',
    { method: 'GET' },
    true,
  )
  return data.jobs || []
}

export async function cancelLocalDownload(id: string): Promise<void> {
  await apiRequest(`/api/local-models/downloads/${encodeURIComponent(id)}/cancel`, { method: 'POST' }, true)
}

export async function retryLocalDownload(id: string): Promise<LocalDownloadJob | null> {
  const data = await apiRequest<{ job: LocalDownloadJob | null }>(
    `/api/local-models/downloads/${encodeURIComponent(id)}/retry`,
    { method: 'POST' },
    true,
  )
  return data.job ?? null
}

export async function deleteLocalDownload(id: string, deleteFiles = true): Promise<void> {
  await apiRequest(
    `/api/local-models/downloads/${encodeURIComponent(id)}?delete_files=${deleteFiles}`,
    { method: 'DELETE' },
    true,
  )
}
