import { apiRequest } from './errors'

// ===== 模型目录类型（对齐后端 app/models/catalog.py 条目）=====

export interface ModelCost {
  input_per_1m: number
  output_per_1m: number
  cache_read_per_1m: number
  cache_write_per_1m: number
}

export interface ModelCapabilities {
  tool_use: boolean
  vision: boolean
  reasoning: boolean
}

export interface ModelInfo {
  id: string
  provider: string
  family: string
  name: string
  description: string
  capabilities: ModelCapabilities
  context_length: number
  limits?: { max_output_tokens?: number } | null
  cost: ModelCost
  default?: boolean
}

export interface ModelCatalogResponse {
  models: ModelInfo[]
  default_model: string | null
  small_model: string | null
  image_caption_model: string | null
  voice_model_size: string | null
}

// 后端模型选择器选项（供组件直接 v-for）
export interface ModelOption {
  value: string
  label: string
  desc: string
}

export function modelsToOptions(models: ModelInfo[]): ModelOption[] {
  return models.map(m => ({
    value: m.id,
    label: m.name || m.id,
    desc: m.description || m.id,
  }))
}

export async function fetchModels(): Promise<ModelCatalogResponse> {
  return apiRequest<ModelCatalogResponse>('/api/models', { method: 'GET' }, true)
}

// ===== 模型管理（前端可配置，持久化到后端 data/model_catalog.db）=====

export interface ProviderInfo {
  provider: string
  label: string
  api_base: string
  api_key: string
  enabled: boolean
  models: ModelInfo[]
}

export interface ModelManagerConfig {
  models: ModelInfo[]
  providers: ProviderInfo[]
  default_model: string | null
  small_model: string | null
  image_caption_model: string | null
  voice_model_size: string | null
  source_path: string
}

export interface TokenEstimate {
  tokens: number
  chars: number
  method: string
}

export async function fetchModelsConfig(): Promise<ModelManagerConfig> {
  return apiRequest<ModelManagerConfig>('/api/models/config', { method: 'GET' }, true)
}

// 按 DeepSeek V4 官方 tokenizer 估算 token（text 或 messages 二选一）
export async function estimateTokens(payload: { text?: string; messages?: unknown[] }): Promise<TokenEstimate> {
  return apiRequest<TokenEstimate>('/api/models/estimate-tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, true)
}

export async function saveCustomModel(entry: Partial<ModelInfo> & { id: string }): Promise<ModelInfo> {
  return apiRequest<ModelInfo>('/api/models/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(entry),
  }, true)
}

export async function deleteCustomModel(mid: string): Promise<{ removed: boolean }> {
  return apiRequest(`/api/models/custom/${encodeURIComponent(mid)}`, { method: 'DELETE' }, true)
}

export async function saveProvider(name: string, data: Partial<ProviderInfo>): Promise<ProviderInfo> {
  return apiRequest<ProviderInfo>(`/api/models/providers/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }, true)
}

export async function deleteProvider(name: string): Promise<{ removed: boolean }> {
  return apiRequest(`/api/models/providers/${encodeURIComponent(name)}`, { method: 'DELETE' }, true)
}

export async function saveDefaults(
  default_model: string | null,
  small_model: string | null,
  image_caption_model: string | null = null,
  voice_model_size: string | null = null,
): Promise<void> {
  await apiRequest('/api/models/defaults', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ default_model, small_model, image_caption_model, voice_model_size }),
  }, true)
}