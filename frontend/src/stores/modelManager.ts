import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  deleteCustomModel,
  deleteProvider,
  fetchModelsConfig,
  saveCustomModel,
  saveDefaults,
  saveProvider,
  type ModelInfo,
  type ModelManagerConfig,
  type ModelOption,
} from '../api/models'
import { useMultiAgentStore } from './multiAgent'

// 模型管理 store：前端配置自定义模型 / Provider / 默认与轻量模型。
// 写操作直落后端 data/model_catalog.json 并即时刷新多 Agent 选择器。
export const useModelManagerStore = defineStore('modelManager', () => {
  const config = ref<ModelManagerConfig | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const notice = ref('')
  let _noticeTimer: ReturnType<typeof setTimeout> | null = null

  function setNotice(msg: string) {
    notice.value = msg
    if (_noticeTimer) clearTimeout(_noticeTimer)
    _noticeTimer = setTimeout(() => { notice.value = '' }, 3500)
  }

  async function load(showError = true) {
    loading.value = true
    error.value = ''
    try {
      config.value = await fetchModelsConfig()
    } catch (e: any) {
      if (showError) error.value = e.message || String(e)
    } finally {
      loading.value = false
    }
  }

  // 所有写操作成功后：刷新本页配置 + 强制刷新多 Agent 选择器
  async function _afterChange() {
    const multi = useMultiAgentStore()
    await Promise.allSettled([load(false), multi.loadModels(true)])
  }

  async function addOrUpdateModel(entry: Partial<ModelInfo> & { id: string }) {
    saving.value = true
    try {
      await saveCustomModel(entry)
      await _afterChange()
      const action = config.value?.models.some(m => m.id === entry.id) ? '更新' : '添加'
      setNotice(`${action}模型成功：${entry.id}`)
      return true
    } catch (e: any) {
      error.value = e.message || String(e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function removeModel(mid: string) {
    saving.value = true
    try {
      await deleteCustomModel(mid)
      await _afterChange()
      setNotice(`已删除模型：${mid}`)
      return true
    } catch (e: any) {
      error.value = e.message || String(e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function updateProvider(name: string, data: Partial<{ label: string; api_base: string; api_key: string; enabled: boolean }>) {
    saving.value = true
    try {
      await saveProvider(name, data)
      await _afterChange()
      setNotice(`已保存 Provider：${name}`)
      return true
    } catch (e: any) {
      error.value = e.message || String(e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function removeProvider(name: string) {
    saving.value = true
    try {
      await deleteProvider(name)
      await _afterChange()
      setNotice(`已删除 Provider：${name}`)
      return true
    } catch (e: any) {
      error.value = e.message || String(e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function setDefaults(
    default_model: string | null,
    small_model: string | null,
    image_caption_model: string | null = null,
    voice_model_size: string | null = null,
  ) {
    saving.value = true
    try {
      await saveDefaults(default_model, small_model, image_caption_model, voice_model_size)
      await _afterChange()
      const multi = useMultiAgentStore()
      // [模型管理] 默认模型变更 → 聊天框选中模型立即跟随（ModelManagerView 也展示小模型）
      if (default_model && multi.modelOptions.some(m => m.value === default_model)) {
        multi.selectedModel = default_model
      }
      setNotice('默认/轻量/图片/语音模型已保存')
      return true
    } catch (e: any) {
      error.value = e.message || String(e)
      return false
    } finally {
      saving.value = false
    }
  }

  // 编辑器模板：按内置条目初始化新表单
  function emptyModel(): Partial<ModelInfo> & { id: string } {
    return {
      id: '',
      provider: 'ollama',
      family: '',
      name: '',
      description: '',
      context_length: 32768,
      capabilities: { tool_use: true, vision: false, reasoning: false },
      cost: { input_per_1m: 0, output_per_1m: 0, cache_read_per_1m: 0, cache_write_per_1m: 0 },
      limits: { max_output_tokens: 8192 },
    }
  }

  const modelOptions = computed<ModelOption[]>(() =>
    (config.value?.models || []).map(m => ({ value: m.id, label: m.name || m.id, desc: m.description || m.id })),
  )

  return {
    config, loading, saving, error, notice,
    load, addOrUpdateModel, removeModel, updateProvider, removeProvider, setDefaults,
    emptyModel, modelOptions,
  }
})