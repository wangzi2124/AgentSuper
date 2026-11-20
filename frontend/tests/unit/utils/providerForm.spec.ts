import { describe, expect, it } from 'vitest'

import {
  PROVIDER_LABEL_MAX_LEN,
  applyProviderPreset,
  inferProviderPreset,
  presetTypes,
  validatePresetForm,
  validateProviderForm,
  type ProviderPresetValues,
} from '../../../src/utils/providerForm'
import type { ProviderPreset } from '../../../src/api/models'

const okBase = { label: '本地 vLLM', name: 'vllm', type: 'openai' as const }

describe('validateProviderForm', () => {
  it('Label 与前缀都必填（不再自动推导）', () => {
    expect(validateProviderForm({ ...okBase, label: '  ' }, false)).toMatchObject({
      ok: false,
      error: '请填写 Label（显示名称）',
    })
    expect(validateProviderForm({ ...okBase, name: '' }, false)).toMatchObject({
      ok: false,
      error: '请填写模型前缀（litellm）',
    })
  })

  it('新增时前缀自动 trim + 小写，并拒绝非法字符', () => {
    const upper = validateProviderForm({ ...okBase, name: '  My-Provider  ' }, false)
    expect(upper.ok).toBe(true)
    expect(upper.name).toBe('my-provider')

    for (const bad of ['my provider', 'my/provider', '中文', '-leading', '_leading']) {
      const r = validateProviderForm({ ...okBase, name: bad }, false)
      expect(r.ok, bad).toBe(false)
    }
  })

  it('允许下划线与连字符，且不能超过 50 字符 Label', () => {
    expect(validateProviderForm({ ...okBase, name: 'ollama_cloud-2' }, false).ok).toBe(true)
    const long = validateProviderForm(
      { ...okBase, label: 'x'.repeat(PROVIDER_LABEL_MAX_LEN + 1) },
      false,
    )
    expect(long.ok).toBe(false)
    expect(long.error).toContain(String(PROVIDER_LABEL_MAX_LEN))
  })

  it('保留前缀三段式：ollama_chat 全拒；ollama 仅 type=ollama 放行', () => {
    for (const type of ['openai', 'ollama'] as const) {
      const r = validateProviderForm({ ...okBase, name: 'ollama_chat', type }, false)
      expect(r.ok, type).toBe(false)
      expect(r.error).toContain('Ollama')
    }
    expect(
      validateProviderForm({ ...okBase, name: 'ollama', type: 'openai' }, false).ok,
    ).toBe(false)
    // type=ollama + 名字 ollama：注册表命中优先于前缀，允许
    expect(
      validateProviderForm({ ...okBase, name: 'ollama', type: 'ollama' }, false).ok,
    ).toBe(true)
  })

  it('编辑时保留存量前缀（不套用 slug 规则、不转小写）', () => {
    const r = validateProviderForm({ ...okBase, name: 'Legacy.Provider' }, true)
    expect(r.ok).toBe(true)
    expect(r.name).toBe('Legacy.Provider')
  })
})

const presets: ProviderPreset[] = [
  { id: 'zhipu', group: 'cn', label: '智谱 GLM', name: 'zhipu', type: 'openai', api_base: 'https://open.bigmodel.cn/api/paas/v4', package: 'openai', litellm_prefix: 'openai' },
  { id: 'ollama-cloud', group: 'local', label: 'Ollama Cloud', name: 'ollama-cloud', type: 'ollama', api_base: 'https://ollama.com', package: 'ollama', litellm_prefix: '' },
  { id: 'custom', group: 'custom', label: '自定义', name: '', type: 'openai', api_base: '', package: 'openai', litellm_prefix: 'openai' },
]

const current: ProviderPresetValues = {
  label: '', name: '', type: '', api_base: '', package: '', litellm_prefix: '',
}

describe('字典驱动的 Provider 预设', () => {
  it('接口类型从字典读取，不硬编码', () => {
    expect(presetTypes(presets)).toEqual(['openai', 'ollama'])
  })

  it('应用预设自动填前缀/类型/api_base/路由前缀，重复时加后缀', () => {
    const applied = applyProviderPreset(presets, 'zhipu', current, ['zhipu'])
    expect(applied).toMatchObject({
      label: '智谱 GLM',
      name: 'zhipu-2',
      type: 'openai',
      api_base: 'https://open.bigmodel.cn/api/paas/v4',
      package: 'openai',
      litellm_prefix: 'openai',
    })
  })

  it('切换预设时不会保留上一项的 litellm_prefix', () => {
    const applied = applyProviderPreset(presets, 'ollama-cloud', {
      ...current,
      litellm_prefix: 'openai',
    })
    expect(applied.litellm_prefix).toBe('')
    expect(applied.api_base).toBe('https://ollama.com')
  })

  it('编辑时按前缀推断字典项', () => {
    expect(inferProviderPreset(presets, { provider: 'ollama-cloud', api_base: 'https://ollama.com' }))
      .toBe('ollama-cloud')
    expect(inferProviderPreset(presets, { provider: 'unknown', api_base: '' })).toBe('custom')
  })

  it('字典表单校验强制路由前缀格式', () => {
    const base = { id: 'x', group: 'custom', label: 'X', name: 'x', type: 'openai', api_base: '', package: 'openai', litellm_prefix: '', note: '' }
    expect(validatePresetForm({ ...base, litellm_prefix: 'Bad Prefix' }, false).ok).toBe(false)
    expect(validatePresetForm({ ...base, litellm_prefix: 'openai' }, false).ok).toBe(true)
  })
})
