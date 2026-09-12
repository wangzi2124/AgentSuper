import type { ModelInfo } from './models'
import type { ProviderInfo } from './models'

// 模型配置前端缓存：后端 model_catalog.db 是唯一事实来源，
// 此处仅是「启动时拉取默认配置后的本地快照」——后端不可达时兜底展示，永远以后端刷新为准。
export interface ModelConfigCache {
  savedAt: number
  models: ModelInfo[]
  default_model: string
  small_model: string
  image_caption_model: string
  voice_model_size: string
  providers?: ProviderInfo[]
  source_path?: string
}

const CACHE_KEY = 'agentsuper:model-config-cache:v1'

export function loadModelCache(): ModelConfigCache | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ModelConfigCache
    if (!parsed || !Array.isArray(parsed.models)) return null
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
      savedAt: cache.savedAt || Date.now(),
      providers: cache.providers ?? prev?.providers,
      source_path: cache.source_path ?? prev?.source_path,
    }
    localStorage.setItem(CACHE_KEY, JSON.stringify(merged))
  } catch {
    // 存储不可用（隐私模式/配额）时静默忽略，后端仍是事实来源
  }
}