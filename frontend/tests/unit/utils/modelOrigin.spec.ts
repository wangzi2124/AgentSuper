import { describe, expect, it } from 'vitest'

import type { ModelInfo, ModelOrigin } from '../../../src/api/models'
import { chatSelectableModels, isManagedModel, managedModels } from '../../../src/utils/modelOrigin'

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

  it('chatSelectableModels 只保留 Provider 模型与显式默认模型', () => {
    const out = chatSelectableModels(
      [
        model('deepseek/deepseek-v4-flash', 'builtin'),
        model('deepseek/deepseek-v4-pro', 'provider'),
        model('openai/gpt-4o', 'builtin'),
        model('ollama_chat/qwen2.5:7b', 'builtin'),
        model('custom/manual', 'extra'),
      ],
      ['deepseek/deepseek-v4-flash', null],
    )
    expect(out.map(m => m.id)).toEqual([
      'deepseek/deepseek-v4-flash',
      'deepseek/deepseek-v4-pro',
      'custom/manual',
    ])
  })

  it('chatSelectableModels 无 Provider 模型时回退全量', () => {
    const all = [model('openai/gpt-4o', 'builtin'), model('local/x', 'local')]
    expect(chatSelectableModels(all, []).map(m => m.id)).toEqual(['openai/gpt-4o', 'local/x'])
  })
})
