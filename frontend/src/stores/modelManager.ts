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
import { loadModelCache, saveModelCache } from '../api/model-cache'

// 模型管理 store：前端配置自定义模型 / Provider / 默认与轻量模型。
// 写操作直落后端 data/model_catalog.db（SQLite，旧 JSON 首启自动导入后仅作备份）并即时刷新多 Agent 选择器。
export const useModelManagerStore = defineStore('modelManager', () => {
  const config = ref<ModelManagerConfig | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  // 写后静默刷新期间的轻量"刷新中"指示（列表区小 spinner，不整页闪）
  const refreshing = ref(false)
  // 正在执行删除/更新的行 id（Provider 用 name），驱动该行按钮 loading
  const busyId = ref<string | null>(null)
  const error = ref('')
  const notice = ref('')
  let _noticeTimer: ReturnType<typeof setTimeout> | null = null
  let _errorTimer: ReturnType<typeof setTimeout> | null = null

  function setNotice(msg: string) {
    notice.value = msg
    if (_noticeTimer) clearTimeout(_noticeTimer)
    _noticeTimer = setTimeout(() => { notice.value = '' }, 3500)
  }

  function setError(msg: string) {
    error.value = msg
    if (_errorTimer) clearTimeout(_errorTimer)
    _errorTimer = setTimeout(() => { error.value = '' }, 6000)
  }

  async function load(showError = true, showLoading = true) {
    if (showLoading) loading.value = true
    error.value = ''
    try {
      config.value = await fetchModelsConfig()
      // 成功后同步本地快照（含 providers/source_path），供离线/重启兜底
      saveModelCache({
        savedAt: Date.now(),
        models: config.value.models,
        default_model: config.value.default_model || '',
        small_model: config.value.small_model || '',
        image_caption_model: config.value.image_caption_model || '',
        voice_model_size: config.value.voice_model_size || '',
        providers: config.value.providers,
        source_path: config.value.source_path,
      })
    } catch (e: any) {
      // 后端不可达：回退本地快照，页面仍可展示（写操作会各自报错）
      const cached = loadModelCache()
      if (cached && cached.models.length) {
        config.value = {
          models: cached.models,
          providers: cached.providers || [],
          default_model: cached.default_model || null,
          small_model: cached.small_model || null,
          image_caption_model: cached.image_caption_model || null,
          voice_model_size: cached.voice_model_size || null,
          source_path: cached.source_path || '',
        }
      } else if (showError && showLoading) {
        error.value = e.message || String(e)
      }
    } finally {
      if (showLoading) loading.value = false
    }
  }

  // 所有写操作成功后：后台静默刷新本页配置 + 强制刷新多 Agent 选择器
  // （不触发整页 loading，避免保存/删除时整个页面闪烁成全屏 spinner；
  //   用 refreshing 在列表区给轻量"刷新中"提示）
  async function _afterChange() {
    refreshing.value = true
    try {
      const multi = useMultiAgentStore()
      await Promise.allSettled([load(false, false), multi.loadModels(true)])
    } finally {
      refreshing.value = false
    }
  }

  async function addOrUpdateModel(entry: Partial<ModelInfo> & { id: string }) {
    saving.value = true
    busyId.value = entry.id
    try {
      const isEdit = (config.value?.models || []).some(m => m.id === entry.id)
      await saveCustomModel(entry)
      await _afterChange()
      setNotice(`${isEdit ? '保存成功（已更新模型）' : '保存成功（新增模型）'}：${entry.id}`)
      return true
    } catch (e: any) {
      setError(e.message || String(e))
      return false
    } finally {
      saving.value = false
      busyId.value = null
    }
  }

  async function removeModel(mid: string) {
    saving.value = true
    busyId.value = mid
    try {
      await deleteCustomModel(mid)
      await _afterChange()
      setNotice(`删除成功：${mid}`)
      return true
    } catch (e: any) {
      setError(e.message || String(e))
      return false
    } finally {
      saving.value = false
      busyId.value = null
    }
  }

  async function updateProvider(name: string, data: Partial<{ label: string; api_base: string; api_key: string; enabled: boolean }>) {
    saving.value = true
    busyId.value = name
    try {
      await saveProvider(name, data)
      await _afterChange()
      setNotice(`保存成功（${name} Provider）`)
      return true
    } catch (e: any) {
      setError(e.message || String(e))
      return false
    } finally {
      saving.value = false
      busyId.value = null
    }
  }

  async function removeProvider(name: string) {
    saving.value = true
    busyId.value = name
    try {
      await deleteProvider(name)
      await _afterChange()
      setNotice(`删除成功（${name} Provider）`)
      return true
    } catch (e: any) {
      setError(e.message || String(e))
      return false
    } finally {
      saving.value = false
      busyId.value = null
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
      setNotice('保存成功（默认/轻量/图片/语音模型）')
      return true
    } catch (e: any) {
      setError(e.message || String(e))
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
    config, loading, saving, refreshing, busyId, error, notice,
    load, addOrUpdateModel, removeModel, updateProvider, removeProvider, setDefaults,
    emptyModel, modelOptions,
  }
})