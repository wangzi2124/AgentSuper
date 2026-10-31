import { describe, expect, it } from 'vitest'

import type { ModelInfo, ModelOrigin } from '../../../src/api/models'
import { isManagedModel, managedModels } from '../../../src/utils/modelOrigin'

function model(id: string, origin?: ModelOrigin): ModelInfo {
  return { id, origin } as ModelInfo
}

describe('modelOrigin', () => {
  it('手动/Provider 来源视为可管理', () => {
    expect(isManagedModel(model('a', 'override'))).toBe(true)
    expect(isManagedModel(model('b', 'extra'))).toBe(true)
    expect(isManagedModel(model('c', 'provider'))).toBe(true)
  })

  it('内置/本机探测/未知来源不进入模型列表', () => {
    expect(isManagedModel(model('a', 'builtin'))).toBe(false)
    expect(isManagedModel(model('b', 'local'))).toBe(false)
    expect(isManagedModel(model('c'))).toBe(false)
  })

  it('managedModels 保持原顺序并过滤掉非管理条目', () => {
    const out = managedModels([
      model('builtin/x', 'builtin'),
      model('p/seen', 'provider'),
      model('ollama_chat/local', 'local'),
      model('custom/manual', 'extra'),
      model('builtin/edited', 'override'),
    ])
    expect(out.map(m => m.id)).toEqual(['p/seen', 'custom/manual', 'builtin/edited'])
  })
})
