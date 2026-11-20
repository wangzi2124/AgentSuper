import type { ProviderPreset } from '../api/models'

export type ProviderType = string

export interface ProviderFormValues {
  label: string
  name: string
  type: ProviderType
}

export interface ProviderValidationResult {
  ok: boolean
  error?: string
  label: string
  name: string
}

export const PROVIDER_NAME_RE = /^[a-z0-9][a-z0-9_-]*$/
export const PROVIDER_LABEL_MAX_LEN = 50

export const PRESET_GROUP_LABELS: Record<string, string> = {
  intl: '国际服务商',
  cn: '国内服务商',
  local: '本地 / 自托管',
  custom: '自定义',
}

export function validateProviderForm(
  values: ProviderFormValues,
  isEdit: boolean,
): ProviderValidationResult {
  const label = String(values.label ?? '').trim()
  let name = String(values.name ?? '').trim()
  if (!isEdit) name = name.toLowerCase()

  if (!label) {
    return { ok: false, error: '请填写 Label（显示名称）', label, name }
  }
  if (label.length > PROVIDER_LABEL_MAX_LEN) {
    return { ok: false, error: `Label 最长 ${PROVIDER_LABEL_MAX_LEN} 个字符`, label, name }
  }

  if (!isEdit) {
    if (!name) {
      return { ok: false, error: '请填写模型前缀（litellm）', label, name }
    }
    if (name.includes('/')) {
      return { ok: false, error: '模型前缀不能包含「/」，它只是 provider 段（如 vllm）', label, name }
    }
    if (!PROVIDER_NAME_RE.test(name)) {
      return {
        ok: false,
        error: '模型前缀只能包含小写字母、数字、下划线和连字符，且必须以字母或数字开头',
        label,
        name,
      }
    }
  }

  const reserved = name === 'ollama_chat' || (name === 'ollama' && values.type !== 'ollama')
  if (reserved) {
    return {
      ok: false,
      error: '保留前缀冲突：Ollama 的 ollama_chat 禁止使用；ollama 仅允许 type=ollama（云端可用 ollama-cloud）。',
      label,
      name,
    }
  }

  return { ok: true, label, name }
}

export interface ProviderPresetValues {
  label: string
  name: string
  type: ProviderType
  api_base: string
  package: string
  litellm_prefix: string
}

const CUSTOM_FALLBACK: ProviderPreset = {
  id: 'custom',
  group: 'custom',
  label: '自定义 / OpenAI 兼容',
  name: '',
  type: 'openai',
  api_base: '',
  package: 'openai',
  litellm_prefix: 'openai',
}

export function presetGroups(presets: ProviderPreset[]): { id: string; label: string }[] {
  const ids = [...new Set(presets.map(p => p.group || 'custom'))]
  if (!ids.includes('custom')) ids.push('custom')
  return ids.map(id => ({ id, label: PRESET_GROUP_LABELS[id] || id }))
}

export function presetTypes(presets: ProviderPreset[]): string[] {
  return [...new Set(presets.map(p => p.type).filter(Boolean))]
}

export function findPreset(presets: ProviderPreset[], id: string): ProviderPreset {
  return presets.find(p => p.id === id) || presets.find(p => p.id === 'custom') || CUSTOM_FALLBACK
}

export function applyProviderPreset(
  presets: ProviderPreset[],
  id: string,
  current: ProviderPresetValues,
  existingNames: string[] = [],
): ProviderPresetValues {
  const preset = findPreset(presets, id)
  let name = preset.name || current.name
  if (preset.name) {
    const taken = new Set(existingNames.map(n => n.toLowerCase()))
    let suffix = 2
    while (taken.has(name.toLowerCase())) {
      name = `${preset.name}-${suffix++}`
    }
  }
  return {
    label: preset.name ? preset.label : current.label,
    name,
    type: preset.type,
    api_base: preset.api_base || (preset.id === 'custom' ? current.api_base : ''),
    package: preset.package,
    litellm_prefix: preset.litellm_prefix,
  }
}

export function inferProviderPreset(
  presets: ProviderPreset[],
  p: { provider: string; api_base: string; type?: ProviderType },
): string {
  const base = (p.api_base || '').toLowerCase()
  const provider = (p.provider || '').toLowerCase()
  const byName = presets.find(x => x.name && x.name.toLowerCase() === provider)
  if (byName) return byName.id
  const byBase = presets.find(x => x.api_base && base.includes(x.api_base.toLowerCase()))
  if (byBase) return byBase.id
  const fallback = presets.find(x => x.id === 'custom')
  return fallback?.id || ''
}

export interface ProviderPresetFormValues {
  id: string
  group: string
  label: string
  name: string
  type: ProviderType
  api_base: string
  package: string
  litellm_prefix: string
  note: string
}

export interface PresetValidationResult {
  ok: boolean
  error?: string
  values: ProviderPresetFormValues
}

export function validatePresetForm(
  raw: ProviderPresetFormValues,
  isEdit: boolean,
): PresetValidationResult {
  const values: ProviderPresetFormValues = {
    id: String(raw.id ?? '').trim().toLowerCase(),
    group: String(raw.group ?? 'custom').trim().toLowerCase() || 'custom',
    label: String(raw.label ?? '').trim(),
    name: String(raw.name ?? '').trim().toLowerCase(),
    type: String(raw.type ?? 'openai').trim().toLowerCase() || 'openai',
    api_base: String(raw.api_base ?? '').trim(),
    package: String(raw.package ?? 'openai').trim().toLowerCase() || 'openai',
    litellm_prefix: String(raw.litellm_prefix ?? '').trim().toLowerCase(),
    note: String(raw.note ?? '').trim(),
  }
  if (!isEdit && !PROVIDER_NAME_RE.test(values.id)) {
    return { ok: false, error: '字典 ID 只能包含小写字母、数字、下划线和连字符', values }
  }
  if (!values.label) {
    return { ok: false, error: '请填写显示名称（label）', values }
  }
  if (values.name && !PROVIDER_NAME_RE.test(values.name)) {
    return { ok: false, error: '模型前缀格式非法', values }
  }
  if (!PROVIDER_NAME_RE.test(values.type)) {
    return { ok: false, error: '接口类型格式非法', values }
  }
  if (values.litellm_prefix && !PROVIDER_NAME_RE.test(values.litellm_prefix)) {
    return { ok: false, error: 'litellm_prefix 格式非法', values }
  }
  return { ok: true, values }
}
