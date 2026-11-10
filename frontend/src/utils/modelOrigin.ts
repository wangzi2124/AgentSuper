import type { ModelInfo, ModelOrigin } from '../api/models'

// 模型管理页「模型列表」只展示用户手动配置（override/extra）或已配置 Provider
// 实际返回的模型；内置目录（builtin）与本机 Ollama 自动探测（local）不展示。
export const MANAGED_ORIGINS: ReadonlySet<ModelOrigin> = new Set(['override', 'extra', 'provider'])

export function isManagedModel(m: ModelInfo): boolean {
  return !!m.origin && MANAGED_ORIGINS.has(m.origin)
}

export function managedModels(models: ModelInfo[]): ModelInfo[] {
  return models.filter(isManagedModel)
}

// 聊天输入框选择器：只保留由已配置 Provider 实际返回/手动配置的模型
// （override/extra/provider），外加显式配置的默认模型，避免把内置目录里
// 与当前 Provider 无关的条目（如只配了 DeepSeek/Ollama 却出现 GPT-4o）塞进下拉。
// 没有任何 Provider 模型时回退全量，避免离线首启选择器为空。
export function chatSelectableModels(
  models: ModelInfo[],
  keepIds: Array<string | null | undefined> = [],
): ModelInfo[] {
  const keep = new Set(
    keepIds.filter((id): id is string => typeof id === 'string' && id.length > 0),
  )
  const selected = models.filter(m => keep.has(m.id) || isManagedModel(m))
  return selected.length ? selected : models
}
