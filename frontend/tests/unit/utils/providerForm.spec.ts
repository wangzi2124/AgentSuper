import { describe, expect, it } from 'vitest'

import {
  PROVIDER_LABEL_MAX_LEN,
  validateProviderForm,
} from '../../../src/utils/providerForm'

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
