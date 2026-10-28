/**
 * Provider 表单校验（与后端 `models/catalog.py` 规则一致）。
 *
 * 规则：Label 与模型前缀都必填、互不推导。
 * - Label：人类可读名称，trim 后非空，≤ 50 字符。
 * - 前缀：litellm provider 段，匹配 `^[a-z0-9][a-z0-9_-]*$`；新增时自动转小写。
 * - Ollama 类型禁止保留前缀 `ollama` / `ollama_chat`。
 * - 编辑已有 Provider 时不校验前缀字符集（存量条目保持原样）。
 */

export type ProviderType = 'openai' | 'ollama'

export interface ProviderFormValues {
  label: string
  name: string
  type: ProviderType
}

export interface ProviderValidationResult {
  ok: boolean
  error?: string
  /** trim 后的 Label（校验通过或失败都返回，便于回填） */
  label: string
  /** trim（新增时转小写）后的前缀 */
  name: string
}

export const PROVIDER_NAME_RE = /^[a-z0-9][a-z0-9_-]*$/
export const PROVIDER_LABEL_MAX_LEN = 50
export const OLLAMA_RESERVED_PREFIXES = ['ollama', 'ollama_chat'] as const

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
    return {
      ok: false,
      error: `Label 最长 ${PROVIDER_LABEL_MAX_LEN} 个字符`,
      label,
      name,
    }
  }

  if (!isEdit) {
    if (!name) {
      return { ok: false, error: '请填写模型前缀（litellm）', label, name }
    }
    if (name.includes('/')) {
      return {
        ok: false,
        error: '模型前缀不能包含「/」，它只是 provider 段（如 vllm）',
        label,
        name,
      }
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

  if (values.type === 'ollama' && (OLLAMA_RESERVED_PREFIXES as readonly string[]).includes(name)) {
    return {
      ok: false,
      error: 'Ollama 类型不能使用保留前缀 ollama / ollama_chat，请改用 ollama-cloud 或 ollama-local',
      label,
      name,
    }
  }

  return { ok: true, label, name }
}
