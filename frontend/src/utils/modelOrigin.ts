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
