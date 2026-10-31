import type { ModelInfo } from './models'
import type { ProviderInfo } from './models'

// 模型配置前端缓存：后端 model_catalog.db 是唯一事实来源，
// 此处仅是「启动时拉取默认配置后的本地快照」——后端不可达时兜底展示，永远以后端刷新为准。
export interface ModelConfigCache {
  version?: number
  savedAt: number
  models: ModelInfo[]
  default_model: string
  small_model: string
  image_caption_model: string
  voice_model_size: string
  providers?: ProviderInfo[]
  source_path?: string
}

// v2：目录条目新增 origin 来源字段（模型管理页据此只展示手动/Provider 模型）。
// 旧 v1 缓存没有来源，直接丢弃，避免把内置模型误当手动模型展示。
export const MODEL_CACHE_VERSION = 2
const CACHE_KEY = 'agentsuper:model-config-cache:v2'
const LEGACY_CACHE_KEYS = ['agentsuper:model-config-cache:v1']

export function loadModelCache(): ModelConfigCache | null {
  try {
    for (const key of LEGACY_CACHE_KEYS) localStorage.removeItem(key)
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ModelConfigCache
    if (!parsed || !Array.isArray(parsed.models)) return null
    if (parsed.version !== MODEL_CACHE_VERSION) return null
    return parsed
  } catch {
    return null
  }
}

export function saveModelCache(cache: ModelConfigCache): void {
  try {
    // 增量写保留未提供字段（聊天层只写 models/defaults，管理页补 providers/source_path）
    const prev = loadModelCache()
    const merged: ModelConfigCache = {
      ...prev,
      ...cache,
      version: MODEL_CACHE_VERSION,
      savedAt: cache.savedAt || Date.now(),
      providers: cache.providers ?? prev?.providers,
      source_path: cache.source_path ?? prev?.source_path,
    }
    localStorage.setItem(CACHE_KEY, JSON.stringify(merged))
  } catch {
    // 存储不可用（隐私模式/配额）时静默忽略，后端仍是事实来源
  }
}