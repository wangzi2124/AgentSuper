<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useModelManagerStore } from '../stores/modelManager'
import { useLocalModelsStore } from '../stores/localModels'
import { fetchModelShow, type ModelInfo, type ProviderInfo, type ProviderPreset } from '../api/models'
import type { LocalDownloadJob } from '../api/localModels'
import {
  applyProviderPreset,
  findPreset,
  inferProviderPreset,
  presetGroups,
  presetTypes,
  PRESET_GROUP_LABELS,
  validatePresetForm,
  validateProviderForm,
  type ProviderPresetFormValues,
} from '../utils/providerForm'
import { managedModels } from '../utils/modelOrigin'

const mm = useModelManagerStore()
const lm = useLocalModelsStore()
const filesPanel = ref<HTMLElement | null>(null)

watch(
  () => lm.repo,
  async (repo) => {
    if (!repo) return
    await nextTick()
    filesPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  },
)

const defaultSel = ref<string>('')
const smallSel = ref<string>('')
const imageSel = ref<string>('')
const voiceSel = ref<string>('')

// [模型管理] 语音合成模型规格（Qwen3-TTS 尺寸）
const VOICE_SIZE_OPTIONS = ['0.6B', '1.7B']

onMounted(async () => {
  await mm.load()
  syncSelectors()
  void lm.init()
})

function syncSelectors() {
  defaultSel.value = mm.config?.default_model || ''
  smallSel.value = mm.config?.small_model || ''
  imageSel.value = mm.config?.image_caption_model || ''
  voiceSel.value = mm.config?.voice_model_size || ''
}

// 默认/轻量/图片模型下拉仍用全量目录；表格只展示手动配置或 Provider 实际返回的模型。
const modelList = computed(() => mm.config?.models || [])
const managedModelList = computed(() => managedModels(modelList.value))

// ── 模型列表：名称/ID 检索 + 分页 ──
const modelQuery = ref('')
const page = ref(1)
const pageSize = ref(10)

const filteredModels = computed(() => {
  const q = modelQuery.value.trim().toLowerCase()
  if (!q) return managedModelList.value
  return managedModelList.value.filter(m =>
    (m.name || '').toLowerCase().includes(q) || m.id.toLowerCase().includes(q)
  )
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredModels.value.length / pageSize.value)))
const pagedModels = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredModels.value.slice(start, start + pageSize.value)
})
watch(modelQuery, () => { page.value = 1 })
watch(pageSize, () => { page.value = 1 })
watch(totalPages, (tp) => { if (page.value > tp) page.value = tp })
function gotoPage(n: number) { page.value = Math.min(Math.max(1, n), totalPages.value) }

// ── 默认/轻量模型 ──
const defaultsSaving = ref(false)
async function saveDefaults() {
  defaultsSaving.value = true
  await mm.setDefaults(
    defaultSel.value || null,
    smallSel.value || null,
    imageSel.value || null,
    voiceSel.value || null,
  )
  defaultsSaving.value = false
  syncSelectors()
}

// ── Provider 编辑 ──
const showProviderForm = ref(false)
// 正在编辑已有 Provider（而非新增）：此时前缀不可改
const providerIsEdit = ref(false)
const providerForm = reactive<{ name: string; label: string; api_base: string; api_key: string; enabled: boolean; type: string; package: string; litellm_prefix: string; compatibility: string; timeout: string; headers: string; body: string }>({
  name: '', label: '', api_base: '', api_key: '', enabled: true, type: '',
  package: '', litellm_prefix: '', compatibility: '', timeout: '', headers: '', body: '',
})
const providerPresetId = ref('')
const presetQuery = ref('')
const presetList = computed(() => mm.presets || [])
const filteredPresetList = computed(() => {
  const q = presetQuery.value.trim().toLowerCase()
  if (!q) return presetList.value
  return presetList.value.filter(p =>
    [p.id, p.label, p.name, p.group, p.type, p.api_base, p.litellm_prefix, p.note]
      .some(value => (value || '').toLowerCase().includes(q)),
  )
})
const presetPage = ref(1)
const presetPageSize = ref(10)
const presetTotalPages = computed(() => Math.max(1, Math.ceil(filteredPresetList.value.length / presetPageSize.value)))
const pagedPresetList = computed(() => {
  const start = (presetPage.value - 1) * presetPageSize.value
  return filteredPresetList.value.slice(start, start + presetPageSize.value)
})
watch(presetQuery, () => { presetPage.value = 1 })
watch(presetPageSize, () => { presetPage.value = 1 })
watch(presetTotalPages, (tp) => { if (presetPage.value > tp) presetPage.value = tp })
function gotoPresetPage(n: number) { presetPage.value = Math.min(Math.max(1, n), presetTotalPages.value) }

const presetGroupOptions = computed(() => presetGroups(presetList.value))
const presetTypeOptions = computed(() => {
  const types = presetTypes(presetList.value)
  if (providerForm.type && !types.includes(providerForm.type)) types.unshift(providerForm.type)
  return types
})
const selectedPreset = computed(() => findPreset(presetList.value, providerPresetId.value))

function openProviderForm(p?: ProviderInfo) {
  providerIsEdit.value = !!p
  providerPresetId.value = p
    ? inferProviderPreset(presetList.value, { provider: p.provider, api_base: p.api_base, type: p.type })
    : (presetList.value.find(x => x.id === 'custom')?.id || 'custom')
  providerForm.name = p?.provider || ''
  providerForm.label = p?.label || ''
  providerForm.api_base = p?.api_base || ''
  providerForm.api_key = p?.api_key || ''
  providerForm.enabled = p?.enabled ?? true
  providerForm.type = p?.type || ''
  providerForm.package = p?.package || ''
  providerForm.litellm_prefix = p?.litellm_prefix || ''
  providerForm.compatibility = p?.compatibility || ''
  providerForm.timeout = p?.timeout ? String(p.timeout) : ''
  providerForm.headers = p?.headers ? JSON.stringify(p.headers, null, 2) : ''
  providerForm.body = p?.body ? JSON.stringify(p.body, null, 2) : ''
  if (!p) applyPresetToForm()
  showProviderForm.value = true
}

function applyPresetToForm() {
  const applied = applyProviderPreset(
    presetList.value,
    providerPresetId.value,
    providerForm,
    (mm.config?.providers || []).map(p => p.provider),
  )
  providerForm.label = applied.label
  if (!providerIsEdit.value) providerForm.name = applied.name
  providerForm.type = applied.type
  providerForm.api_base = applied.api_base
  providerForm.package = applied.package
  providerForm.litellm_prefix = applied.litellm_prefix
}

function onProviderPresetChange() {
  applyPresetToForm()
}
async function submitProvider() {
  if (!providerForm.api_base.trim()) {
    mm.setError(`请填写 api_base${selectedPreset.value.api_base ? `（如 ${selectedPreset.value.api_base}）` : ''}`)
    return
  }
  // Label 与模型前缀都必填、互不推导（规则见 utils/providerForm.ts）
  const validated = validateProviderForm(
    { label: providerForm.label, name: providerForm.name, type: providerForm.type },
    providerIsEdit.value,
  )
  if (!validated.ok) {
    mm.setError(validated.error || 'Provider 表单校验失败')
    return
  }
  providerForm.label = validated.label
  providerForm.name = validated.name
  // 仅在保存成功时关闭弹窗：失败时保留用户输入，否则错误 toast 容易被错过
  let headers: Record<string, string> | undefined
  let body: Record<string, unknown> | undefined
  try {
    headers = providerForm.headers.trim() ? JSON.parse(providerForm.headers) : undefined
    body = providerForm.body.trim() ? JSON.parse(providerForm.body) : undefined
  } catch {
    mm.setError('headers / body 必须是合法 JSON')
    return
  }
  const ok = await mm.updateProvider(validated.name, {
    label: validated.label,
    api_base: providerForm.api_base,
    api_key: providerForm.api_key,
    enabled: providerForm.enabled,
    type: providerForm.type,
    package: providerForm.package.trim() || selectedPreset.value.package,
    litellm_prefix: providerForm.litellm_prefix.trim(),
    compatibility: providerForm.compatibility.trim(),
    // v-model 对 type="number" 输入会把值转成 number，统一按 String 处理避免 .trim() 崩溃
    timeout: String(providerForm.timeout).trim() ? Number(providerForm.timeout) : 0,
    headers,
    body,
  })
  if (ok) showProviderForm.value = false
}
async function toggleProvider(p: ProviderInfo) {
  await mm.updateProvider(p.provider, { enabled: !p.enabled })
}
async function delProvider(p: ProviderInfo) {
  if (!window.confirm(`删除 Provider「${p.label || p.provider}」？其自动注册的模型会一并消失。`)) return
  await mm.removeProvider(p.provider)
}

const showPresetForm = ref(false)
const presetIsEdit = ref(false)
const presetForm = reactive<ProviderPresetFormValues>({
  id: '', group: 'custom', label: '', name: '', type: '',
  api_base: '', package: '', litellm_prefix: '', note: '',
})

function openPresetForm(p?: ProviderPreset) {
  presetIsEdit.value = !!p
  presetForm.id = p?.id || ''
  presetForm.group = p?.group || 'custom'
  presetForm.label = p?.label || ''
  presetForm.name = p?.name || ''
  presetForm.type = p?.type || ''
  presetForm.api_base = p?.api_base || ''
  presetForm.package = p?.package || ''
  presetForm.litellm_prefix = p?.litellm_prefix || ''
  presetForm.note = p?.note || ''
  showPresetForm.value = true
}

async function submitPreset() {
  const validated = validatePresetForm(presetForm, presetIsEdit.value)
  if (!validated.ok) {
    mm.setError(validated.error || '供应商字典校验失败')
    return
  }
  const ok = await mm.addOrUpdatePreset({ ...validated.values }, presetIsEdit.value)
  if (ok) showPresetForm.value = false
}

async function delPreset(p: ProviderPreset) {
  if (!window.confirm(`从供应商字典删除「${p.label}」（${p.id}）？已有 Provider 不受影响。`)) return
  await mm.removePreset(p.id)
}

// ── 自定义模型编辑 ──
const showModelForm = ref(false)
const modelInspecting = ref(false)
let modelInspectSeq = 0
const modelForm = reactive<{
  id: string; name: string; provider: string; family: string; description: string;
  context_length: number; max_output_tokens: number;
  input_price: number; output_price: number; cache_read_price: number; cache_write_price: number;
  tool_use: boolean; vision: boolean; reasoning: boolean; thinking: string; variants: string;
}>({
  id: '', name: '', provider: 'ollama', family: '', description: '',
  context_length: 32768, max_output_tokens: 8192,
  input_price: 0, output_price: 0, cache_read_price: 0, cache_write_price: 0,
  tool_use: true, vision: false, reasoning: false, thinking: '', variants: '',
})

function applyModelForm(m?: ModelInfo) {
  modelForm.name = m?.name || ''
  modelForm.provider = m?.provider || 'ollama'
  modelForm.family = m?.family || m?.id?.split('/')[1] || ''
  modelForm.description = m?.description || ''
  modelForm.context_length = m?.context_length || 32768
  modelForm.max_output_tokens = m?.limits?.max_output_tokens || 8192
  modelForm.input_price = m?.cost?.input_per_1m ?? 0
  modelForm.output_price = m?.cost?.output_per_1m ?? 0
  modelForm.cache_read_price = m?.cost?.cache_read_per_1m ?? 0
  modelForm.cache_write_price = m?.cost?.cache_write_per_1m ?? 0
  modelForm.tool_use = m?.capabilities?.tool_use ?? true
  modelForm.vision = m?.capabilities?.vision ?? false
  modelForm.reasoning = m?.capabilities?.reasoning ?? false
  modelForm.thinking = m?.thinking || ''
  modelForm.variants = m?.variants ? JSON.stringify(m.variants, null, 2) : ''
}

// 重新添加内置/已探测模型时，按 id 自动带出目录参数，避免用表单默认值覆盖真实窗口/能力。
const modelFormCatalogId = ref('')
watch(() => modelForm.id, (raw) => {
  if (!showModelForm.value) return
  const id = raw.trim()
  if (!id || id === modelFormCatalogId.value) return
  const known = modelList.value.find(m => m.id === id)
  if (!known) return
  modelFormCatalogId.value = id
  applyModelForm(known)
})

async function openModelForm(m?: ModelInfo) {
  modelForm.id = m?.id || ''
  modelFormCatalogId.value = m?.id || ''
  applyModelForm(m)
  showModelForm.value = true
  if (!m) return
  const id = m.id
  const seq = ++modelInspectSeq
  modelInspecting.value = true
  try {
    const shown = await fetchModelShow(id)
    if (seq !== modelInspectSeq || !showModelForm.value || modelForm.id.trim() !== id) return
    if (shown.capabilities) {
      modelForm.tool_use = shown.capabilities.tool_use
      modelForm.vision = shown.capabilities.vision
      modelForm.reasoning = shown.capabilities.reasoning
    }
    if (shown.context_length) modelForm.context_length = shown.context_length
  } catch {
    // 非 Ollama 模型没有 /api/show；保留目录里的编辑值。
  } finally {
    if (seq === modelInspectSeq) modelInspecting.value = false
  }
}

async function submitModel() {
  if (!modelForm.id.trim()) return
  let variants: Record<string, unknown> | undefined
  try {
    variants = modelForm.variants.trim() ? JSON.parse(modelForm.variants) : undefined
  } catch {
    mm.setError('variants 必须是合法 JSON')
    return
  }
  const entry: Partial<ModelInfo> & { id: string } = {
    id: modelForm.id.trim(),
    name: modelForm.name.trim() || modelForm.id.trim(),
    provider: modelForm.provider.trim() || (modelForm.id.split('/')[0] || ''),
    family: modelForm.family.trim() || modelForm.id.split('/')[1] || '',
    description: modelForm.description,
    context_length: Number(modelForm.context_length) || 32768,
    limits: { max_output_tokens: Number(modelForm.max_output_tokens) || 8192 },
    capabilities: { tool_use: modelForm.tool_use, vision: modelForm.vision, reasoning: modelForm.reasoning },
    thinking: modelForm.thinking || undefined,
    variants: variants as Record<string, Record<string, unknown>> | undefined,
    cost: {
      input_per_1m: Number(modelForm.input_price) || 0,
      output_per_1m: Number(modelForm.output_price) || 0,
      cache_read_per_1m: Number(modelForm.cache_read_price) || 0,
      cache_write_per_1m: Number(modelForm.cache_write_price) || 0,
    },
  }
  // 仅在保存成功时关闭弹窗：失败时保留用户输入，否则错误 toast 容易被错过
  const ok = await mm.addOrUpdateModel(entry)
  if (ok) showModelForm.value = false
}
async function delModel(m: ModelInfo) {
  if (!window.confirm(`删除模型「${m.name || m.id}」？`)) return
  await mm.removeModel(m.id)
}

function fmtCost(v?: number) {
  if (!v) return '免费'
  return `$${v} /1M`
}
function capOf(m: ModelInfo) {
  const c = m.capabilities || {}
  const think = m.thinking === 'enabled' ? '思考' : ''
  return [c.tool_use && '工具', c.vision && '视觉', c.reasoning && '推理', think].filter(Boolean).join('·') || '—'
}

// ── 本地模型下载 ──
function fmtSize(bytes?: number) {
  const n = Number(bytes || 0)
  if (!n) return '未知大小'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`
}
function fmtSpeed(bytes?: number) {
  return bytes ? `${fmtSize(bytes)}/s` : ''
}
const JOB_STATE_LABEL: Record<string, string> = {
  pending: '排队中',
  downloading: '下载中',
  importing: '导入中',
  done: '已完成',
  error: '失败',
  cancelled: '已取消',
}
function jobStateLabel(state: string) {
  return JOB_STATE_LABEL[state] || state
}
function activeState(state: string) {
  return state === 'pending' || state === 'downloading' || state === 'importing'
}
function removeJob(job: LocalDownloadJob) {
  const msg = job.state === 'done'
    ? '删除任务、下载文件，并移除已导入的 Ollama 模型？'
    : '删除任务并清理已下载文件？'
  if (!window.confirm(msg)) return
  void lm.remove(job)
}
</script>

<template>
  <div class="page-header">
    <h2>模型管理</h2>
    <p>前端配置自定义模型/服务商，持久化到 {{ mm.config?.source_path || 'data/model_catalog.db' }}，无需改后端 .env
      （旧 model_catalog.json 首启自动导入后仅作迁移备份）</p>
  </div>

  <div class="page-content">
    <Transition name="toast">
      <div v-if="mm.notice" class="mm-toast mm-toast--ok">{{ mm.notice }}</div>
    </Transition>
    <Transition name="toast">
      <div v-if="mm.error" class="mm-toast mm-toast--err">{{ mm.error }}</div>
    </Transition>

    <div v-if="mm.loading" class="loading-wrap"><span class="spinner"></span></div>
    <div v-else class="mm-wrap">
      <!-- 默认/轻量模型 -->
      <section class="card mm-block">
        <div class="mm-defaults-row">
          <label class="mm-pill-field">
            <span class="mm-pill-caption">默认模型</span>
            <span class="mm-pill">
              <svg class="mm-pill-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z"/></svg>
              <select v-model="defaultSel" class="mm-pill-select">
                <option v-for="m in modelList" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
              <svg class="mm-pill-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </span>
          </label>
          <label class="mm-pill-field">
            <span class="mm-pill-caption">轻量模型</span>
            <span class="mm-pill">
              <svg class="mm-pill-icon mm-pill-icon-two" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z"/></svg>
              <select v-model="smallSel" class="mm-pill-select">
                <option value="">（与默认相同，不指定）</option>
                <option v-for="m in modelList" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
              <svg class="mm-pill-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </span>
          </label>
          <label class="mm-pill-field">
            <span class="mm-pill-caption">图片解析模型</span>
            <span class="mm-pill">
              <svg class="mm-pill-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4.5 18.5l5-5 2.8 2.8 3.2-3.2 4 4.4"/></svg>
              <select v-model="imageSel" class="mm-pill-select">
                <option value="">（跟随后端配置）</option>
                <option v-for="m in modelList" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
              <svg class="mm-pill-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </span>
          </label>
          <label class="mm-pill-field">
            <span class="mm-pill-caption">语音模型</span>
            <span class="mm-pill">
              <svg class="mm-pill-icon mm-pill-icon-two" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/><path d="M8 21h8"/></svg>
              <select v-model="voiceSel" class="mm-pill-select">
                <option value="">（跟随后端配置）</option>
                <option v-for="sz in VOICE_SIZE_OPTIONS" :key="sz" :value="sz">{{ sz }}</option>
              </select>
              <svg class="mm-pill-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </span>
          </label>
          <div class="mm-defaults-save">
            <button class="btn btn-primary mm-save-btn" :disabled="mm.saving || defaultsSaving" @click="saveDefaults">
              <span v-if="defaultsSaving" class="spinner-mm"></span>
              {{ defaultsSaving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
        <p class="hint mm-defaults-hint">轻量模型用于内部轻任务（子任务分类/会话标题等）；图片解析模型用于图像描述（可选全部 Provider 模型，如 ollama/llava）；语音模型为 Qwen3-TTS 合成规格（0.6B/1.7B）。不指定则跟随后端 .env；改动保存后聊天框立即跟随。</p>
      </section>

      <!-- 供应商字典 -->
      <section class="card mm-block">
        <div class="form-title">
          供应商字典
          <span v-if="mm.presetsLoading" class="spinner-mm mm-refresh-spin" title="加载中"></span>
          <button class="btn btn-primary mm-title-action" @click="openPresetForm()">+ 添加供应商</button>
        </div>
        <p class="hint">字典决定「新增 Provider」时自动填入的前缀、接口类型、api_base 与 litellm 路由前缀；修改字典不影响已有 Provider。</p>
        <div class="mm-list-toolbar">
          <label class="mm-search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
            <input v-model="presetQuery" placeholder="按名称 / ID / 类型检索供应商字典…" />
          </label>
          <span class="mm-count">{{ filteredPresetList.length }} / {{ presetList.length }}</span>
        </div>
        <div v-if="!filteredPresetList.length" class="empty-tip">{{ presetQuery ? '未找到匹配的供应商字典项' : (mm.presetsLoading ? '字典加载中…' : '暂无字典项，请添加') }}</div>
        <div class="mm-table preset-table">
          <div v-if="filteredPresetList.length" class="mm-th">
            <span>名称 / ID</span><span>分组 / 前缀</span><span>接口类型</span><span>api_base</span><span class="mm-ops">操作</span>
          </div>
          <div v-for="p in pagedPresetList" :key="p.id" class="mm-tr">
            <span class="mm-main">{{ p.label }} <small class="mm-mono">{{ p.id }}</small></span>
            <span>{{ PRESET_GROUP_LABELS[p.group] || p.group }} / <code>{{ p.name || '—' }}</code></span>
            <span>{{ p.type }}</span>
            <span class="mm-mono">{{ p.api_base || '—' }}</span>
            <span class="mm-ops">
              <button class="btn btn-sm" :disabled="mm.presetBusyId === p.id" @click="openPresetForm(p)">编辑</button>
              <button class="btn btn-sm btn-danger" :disabled="!!mm.presetBusyId" @click="delPreset(p)">删除</button>
            </span>
          </div>
        </div>
        <div v-if="filteredPresetList.length" class="mm-pager preset-pager">
          <span class="mm-pager-info">第 {{ presetPage }} / {{ presetTotalPages }} 页</span>
          <label class="mm-pagesize">每页
            <select v-model.number="presetPageSize">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
            </select>
          </label>
          <button class="btn btn-sm" :disabled="presetPage <= 1" @click="gotoPresetPage(presetPage - 1)">上一页</button>
          <button class="btn btn-sm" :disabled="presetPage >= presetTotalPages" @click="gotoPresetPage(presetPage + 1)">下一页</button>
        </div>
      </section>

      <!-- Providers -->
      <section class="card mm-block">
        <div class="form-title">
          模型服务商 Provider
          <span v-if="mm.refreshing" class="spinner-mm mm-refresh-spin" title="刷新中"></span>
          <button class="btn btn-primary mm-title-action" @click="openProviderForm()">+ 新增 Provider</button>
        </div>
        <p class="hint">provider 的 api_base 指向 OpenAI 兼容服务（vLLM / LM Studio / 私服）；保存后自动探测其模型并注册。</p>
        <div v-if="!mm.config?.providers?.length" class="empty-tip">暂无自定义 Provider</div>
        <div class="mm-table provider-table">
          <div class="mm-th"><span>名称</span><span>api_base</span><span>模型数</span><span>启用</span><span class="mm-ops">操作</span></div>
          <div v-for="p in mm.config?.providers" :key="p.provider" class="mm-tr">
            <span class="mm-main">{{ p.label || p.provider }}</span>
            <span class="mm-mono">{{ p.api_base }}</span>
            <span>{{ p.models?.length ?? 0 }}</span>
            <span>
              <label class="switch">
                <input type="checkbox" :checked="p.enabled" :disabled="!!mm.busyId" @change="toggleProvider(p)" />
                <span class="slider"></span>
              </label>
            </span>
            <span class="mm-ops">
              <button class="btn btn-sm" :disabled="mm.busyId === p.provider" @click="openProviderForm(p)">编辑</button>
              <button class="btn btn-sm btn-danger" :disabled="mm.busyId === p.provider || !!mm.busyId" @click="delProvider(p)">
                <span v-if="mm.busyId === p.provider" class="spinner-mm"></span>
                {{ mm.busyId === p.provider ? '删除中…' : '删除' }}
              </button>
            </span>
          </div>
        </div>
      </section>

      <!-- 本地模型下载 -->
      <section class="card mm-block">
        <div class="form-title">
          本地模型
          <span v-if="lm.activeJobs.length" class="spinner-mm mm-refresh-spin" title="下载中"></span>
        </div>
        <p class="hint">从 ModelScope（优先，失败自动回落 HuggingFace）搜索 GGUF，下载后自动导入本机 Ollama，并注册进模型列表，可直接在聊天框选择。</p>
        <p v-if="lm.error" class="lm-error">{{ lm.error }}</p>
        <div class="mm-list-toolbar">
          <label class="mm-search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            <input
              v-model="lm.query"
              placeholder="搜索 GGUF 模型，如 qwen2.5-7b-instruct-gguf"
              @keyup.enter="lm.search()"
            />
          </label>
          <select v-model="lm.source" class="ctrl lm-source">
            <option value="auto">自动（按优先级）</option>
            <option v-for="s in lm.priority" :key="s" :value="s">{{ s }}</option>
          </select>
          <button class="btn btn-primary mm-save-btn" :disabled="lm.searching || !lm.query.trim()" @click="lm.search()">
            <span v-if="lm.searching" class="spinner-mm"></span>
            {{ lm.searching ? '搜索中…' : '搜索' }}
          </button>
        </div>

        <div v-if="lm.results.length" class="mm-table lm-table">
          <div class="mm-th"><span>模型</span><span>来源</span><span class="mm-num">下载量</span><span></span></div>
          <div
            v-for="r in lm.results"
            :key="r.source + '/' + r.id"
            class="mm-tr"
            :class="{ 'lm-row-active': lm.repo?.id === r.id }"
          >
            <span class="mm-main">{{ r.name || r.id }}<span class="lm-sub">{{ r.id }}</span></span>
            <span class="mm-mono">{{ r.source }}</span>
            <span class="mm-num">{{ r.downloads.toLocaleString() }}</span>
            <span class="mm-ops"><button class="btn btn-sm" @click="lm.openRepo(r)">查看文件</button></span>
          </div>
        </div>

        <div v-if="lm.results.length" class="lm-more">
          <span class="mm-count">第 {{ lm.page }} 页 · 已加载 {{ lm.results.length }} 个模型</span>
          <button
            v-if="lm.hasMore"
            class="btn btn-sm lm-more-btn"
            :disabled="lm.loadingMore"
            @click="lm.loadMore()"
          >
            <span v-if="lm.loadingMore" class="spinner-mm"></span>
            {{ lm.loadingMore ? '加载中…' : '加载更多' }}
          </button>
          <span v-else class="hint">已加载全部结果</span>
        </div>

        <div v-if="lm.repo" ref="filesPanel" class="lm-files">
          <div class="lm-files-head">
            <strong>{{ lm.repo.id }}</strong>
            <span v-if="lm.filesLoading" class="spinner-mm"></span>
            <span v-else class="mm-count">{{ lm.files.length }} 个 GGUF 文件</span>
          </div>
          <div v-if="!lm.filesLoading && !lm.files.length" class="empty-tip">该仓库没有 GGUF 文件，换一个试试。</div>
          <div v-if="lm.files.length" class="mm-table lm-file-table">
            <div class="mm-th"><span>文件</span><span class="mm-num">大小</span><span class="mm-num">量化</span><span></span></div>
            <label
              v-for="f in lm.files"
              :key="f.file"
              class="mm-tr lm-file-row"
              :class="{ 'lm-row-active': lm.selectedFile === f.file }"
            >
              <span class="mm-main">{{ f.file }}<span v-if="f.split" class="lm-sub">分片 {{ f.parts }} 个，自动全部下载</span></span>
              <span class="mm-num">{{ fmtSize(f.size) }}</span>
              <span class="mm-num">{{ f.quantization || '—' }}</span>
              <span class="mm-ops"><input v-model="lm.selectedFile" type="radio" :value="f.file" /></span>
            </label>
          </div>
          <div v-if="lm.files.length" class="lm-actions">
            <button class="btn btn-primary mm-save-btn" :disabled="!lm.selectedEntry || !!lm.busyId" @click="lm.startDownload">
              <span v-if="lm.busyId" class="spinner-mm"></span>
              {{ lm.busyId ? '提交中…' : '下载并导入' }}
            </button>
            <span class="hint">下载后通过本机 Ollama <code>/api/create</code> 导入；大型模型请确认磁盘空间。</span>
          </div>
        </div>

        <div v-if="lm.jobs.length" class="lm-jobs">
          <div class="form-title small">下载任务</div>
          <div v-for="j in lm.jobs" :key="j.id" class="lm-job">
            <div class="lm-job-main">
              <span class="mm-main">{{ j.entry?.name || j.file }}</span>
              <span class="lm-job-state" :class="'lm-state-' + j.state">{{ jobStateLabel(j.state) }}</span>
              <span class="mm-count">{{ fmtSize(j.downloaded) }} / {{ fmtSize(j.total) }}<template v-if="j.speed"> · {{ fmtSpeed(j.speed) }}</template></span>
            </div>
            <div class="lm-bar">
              <div
                class="lm-bar-fill"
                :class="{ 'lm-bar-done': j.state === 'done' }"
                :style="{ width: (j.state === 'done' ? 100 : j.percent) + '%' }"
              ></div>
            </div>
            <div class="lm-job-foot">
              <span v-if="j.error" class="lm-job-error">{{ j.error }}</span>
              <span v-else-if="j.state === 'done'" class="hint">已注册：{{ j.entry_id }}</span>
              <span v-else class="hint">{{ j.percent }}%</span>
              <span class="mm-ops">
                <button v-if="activeState(j.state)" class="btn btn-sm" :disabled="lm.busyId === j.id" @click="lm.cancel(j)">取消</button>
                <button v-if="j.state === 'error'" class="btn btn-sm" :disabled="lm.busyId === j.id" @click="lm.retry(j)">重试导入</button>
                <button class="btn btn-sm btn-danger" :disabled="lm.busyId === j.id" @click="removeJob(j)">删除</button>
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- 模型列表 -->
      <section class="card mm-block">
        <div class="form-title">
          模型列表
          <span v-if="mm.refreshing" class="spinner-mm mm-refresh-spin" title="刷新中"></span>
          <button class="btn btn-primary mm-title-action" @click="openModelForm()">+ 自定义模型</button>
        </div>
        <p class="hint">仅显示手动添加/编辑的模型与已配置 Provider 实际返回的模型；内置模型仍可在上方默认模型下拉中选择。要在此管理内置/本机模型，请重新添加其 ID（自动带出目录参数），或配置一个 ollama 类型 Provider。</p>
        <div class="mm-list-toolbar">
          <label class="mm-search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            <input v-model="modelQuery" placeholder="按名称 / ID 检索模型…" />
          </label>
          <span class="mm-count">共 {{ filteredModels.length }} 个模型</span>
        </div>
        <div class="mm-table">
          <div class="mm-th"><span>名称</span><span>模型 ID</span><span class="mm-num">上下文</span><span class="mm-num">输入 / 输出价格</span><span>能力</span><span class="mm-ops">操作</span></div>
          <div v-for="m in pagedModels" :key="m.id" class="mm-tr">
            <span class="mm-main">{{ m.name || m.id }}</span>
            <span class="mm-mono mm-id">{{ m.id }}</span>
            <span class="mm-num">{{ m.context_length.toLocaleString() }}</span>
            <span class="mm-num">{{ fmtCost(m.cost?.input_per_1m) }} / {{ fmtCost(m.cost?.output_per_1m) }}</span>
            <span class="mm-cap">{{ capOf(m) }}</span>
            <span class="mm-ops">
              <button class="btn btn-sm" :disabled="mm.busyId === m.id || !!mm.busyId" @click="openModelForm(m)">编辑</button>
              <button class="btn btn-sm btn-danger" :disabled="mm.busyId === m.id || !!mm.busyId" @click="delModel(m)">
                <span v-if="mm.busyId === m.id" class="spinner-mm"></span>
                {{ mm.busyId === m.id ? '删除中…' : '删除' }}
              </button>
            </span>
          </div>
          <div v-if="!filteredModels.length" class="empty-tip">{{ modelQuery ? '未找到匹配的模型' : '暂无自定义或 Provider 模型' }}</div>
        </div>
        <div v-if="filteredModels.length" class="mm-pager">
          <span class="mm-pager-info">第 {{ page }} / {{ totalPages }} 页</span>
          <label class="mm-pagesize">每页
            <select v-model.number="pageSize">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
            </select>
          </label>
          <button class="btn btn-sm" :disabled="page <= 1" @click="gotoPage(page - 1)">上一页</button>
          <button class="btn btn-sm" :disabled="page >= totalPages" @click="gotoPage(page + 1)">下一页</button>
        </div>
      </section>
    </div>
  </div>

  <div v-if="showPresetForm" class="overlay">
    <div class="dialog wide">
      <div class="form-title">{{ presetIsEdit ? '编辑供应商字典项' : '新增供应商字典项' }}</div>
      <div class="form-row">
        <label class="mm-field"><span>字典 ID</span>
          <input v-model="presetForm.id" class="ctrl" placeholder="如 zhipu / my_provider" :disabled="presetIsEdit" />
        </label>
        <label class="mm-field"><span>显示名称</span>
          <input v-model="presetForm.label" class="ctrl" placeholder="智谱 GLM" maxlength="50" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field"><span>分组</span>
          <input v-model="presetForm.group" class="ctrl" list="preset-group-list" placeholder="intl / cn / local / custom" />
          <datalist id="preset-group-list">
            <option v-for="g in presetGroupOptions" :key="g.id" :value="g.id">{{ g.label }}</option>
          </datalist>
        </label>
        <label class="mm-field"><span>模型前缀</span>
          <input v-model="presetForm.name" class="ctrl" placeholder="如 zhipu（空 = 自定义）" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field"><span>接口类型</span>
          <input v-model="presetForm.type" class="ctrl" list="preset-type-list" placeholder="openai / ollama" />
          <datalist id="preset-type-list">
            <option v-for="t in presetTypeOptions" :key="t" :value="t" />
          </datalist>
        </label>
        <label class="mm-field"><span>package</span>
          <input v-model="presetForm.package" class="ctrl" placeholder="openai" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>api_base</span>
          <input v-model="presetForm.api_base" class="ctrl" placeholder="https://api.example.com/v1" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>litellm_prefix（强制路由前缀）</span>
          <input v-model="presetForm.litellm_prefix" class="ctrl" placeholder="如 openai；空 = 按前缀原生路由" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>说明</span>
          <input v-model="presetForm.note" class="ctrl" placeholder="显示在新增 Provider 表单中" />
        </label>
      </div>
      <div class="form-footer">
        <button class="btn" @click="showPresetForm = false">取消</button>
        <button class="btn btn-primary mm-save-btn" :disabled="!!mm.presetBusyId" @click="submitPreset">
          <span v-if="mm.presetBusyId" class="spinner-mm"></span>
          {{ mm.presetBusyId ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </div>

  <!-- Provider 编辑弹窗 -->
  <div v-if="showProviderForm" class="overlay">
    <div class="dialog">
       <div class="form-title">{{ providerIsEdit ? '编辑 Provider' : '新增 Provider' }}</div>
       <div class="form-row">
         <label class="mm-field mm-wide"><span>服务商预设</span>
           <select v-model="providerPresetId" class="ctrl" @change="onProviderPresetChange">
             <optgroup v-for="g in presetGroupOptions" :key="g.id" :label="g.label">
               <option v-for="p in presetList.filter(x => x.group === g.id)" :key="p.id" :value="p.id">{{ p.label }}</option>
             </optgroup>
           </select>
         </label>
       </div>
       <p v-if="selectedPreset.note" class="hint provider-prefix-hint">{{ selectedPreset.note }}</p>
       <div class="form-row">
         <label class="mm-field mm-wide"><span>Label（显示名称）</span>

          <input v-model="providerForm.label" class="ctrl" placeholder="本地 vLLM" maxlength="50" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide">
          <span>模型前缀（litellm）<template v-if="providerIsEdit"> — 不可修改</template></span>
          <input
            v-model="providerForm.name"
            class="ctrl"
            placeholder="如 vllm / ollama-cloud / my_provider"
            :disabled="providerIsEdit"
          />
        </label>
      </div>
       <p class="hint provider-prefix-hint">模型 id = <code>前缀/模型名</code>，前缀决定模型列表里的 id（如 <code>vllm/deepseek-r1:latest</code>）。前缀只能包含小写字母、数字、<code>_</code>、<code>-</code>。<code>google</code> 前缀会自动适配 Gemini（兼容端点走 <code>openai/*</code>，原生 API 走 <code>gemini/*</code>）。</p>

       <div class="form-row">
         <label class="mm-field mm-wide"><span>接口类型（来自字典）</span>
           <select v-model="providerForm.type" class="ctrl">
             <option v-for="t in presetTypeOptions" :key="t" :value="t">{{ t }}</option>
           </select>
         </label>
       </div>

       <div class="form-row">
         <label class="mm-field"><span>package</span>
           <input v-model="providerForm.package" class="ctrl" placeholder="openai / anthropic / google" />
         </label>
         <label class="mm-field"><span>litellm 路由前缀（可选）</span>
           <input v-model="providerForm.litellm_prefix" class="ctrl" placeholder="字典自动填入；空 = 不改写" />
         </label>
         <label class="mm-field"><span>compatibility</span>

          <input v-model="providerForm.compatibility" class="ctrl" placeholder="可选" />
        </label>
        <label class="mm-field"><span>timeout（秒）</span>
          <input v-model="providerForm.timeout" class="ctrl" type="number" min="1" placeholder="默认" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>headers（JSON）</span>
          <textarea v-model="providerForm.headers" class="ctrl" rows="3" placeholder='{"X-Custom": "1"}' />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>body（JSON，合并到请求体）</span>
          <textarea v-model="providerForm.body" class="ctrl" rows="3" placeholder='{"some_flag": true}' />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>api_base</span>
          <input
            v-model="providerForm.api_base"
            class="ctrl"
            :placeholder="selectedPreset.api_base || 'https://api.example.com/v1'"
          />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>api_key（可选）</span>
          <input v-model="providerForm.api_key" class="ctrl" type="password" placeholder="sk-...（本地服务可留空）" />
        </label>
      </div>
      <div class="form-footer">
        <label><input v-model="providerForm.enabled" type="checkbox" /> 启用（默认开启探测）</label>
        <button class="btn" :disabled="mm.saving" @click="showProviderForm = false">取消</button>
        <button class="btn btn-primary mm-save-btn" :disabled="mm.saving" @click="submitProvider">
          <span v-if="mm.saving" class="spinner-mm"></span>
          {{ mm.saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </div>

  <!-- 自定义模型编辑弹窗 -->
  <div v-if="showModelForm" class="overlay">
    <div class="dialog wide">
      <div class="form-title">
        {{ modelForm.id.trim() && modelList.find(m => m.id === modelForm.id.trim()) ? '编辑模型' : '新增自定义模型' }}
        <span v-if="modelInspecting" class="hint">正在通过 Ollama /api/show 读取能力…</span>
      </div>
      <div class="form-row">
        <label class="mm-field"><span>模型 ID（provider/model）</span>
          <input v-model="modelForm.id" class="ctrl" placeholder="ollama/qwen2.5-coder:latest" />
        </label>
        <label class="mm-field"><span>显示名称</span><input v-model="modelForm.name" class="ctrl" placeholder="Ollama Qwen2.5-Coder" /></label>
      </div>
      <div class="form-row">
        <label class="mm-field"><span>provider</span>
          <input v-model="modelForm.provider" class="ctrl" placeholder="ollama / deepseek / openai / 自定义" list="provider-list" />
          <datalist id="provider-list">
            <option v-for="p in mm.config?.providers" :key="p.provider" :value="p.provider" />
            <option value="ollama" /><option value="deepseek" /><option value="openai" />
          </datalist>
        </label>
        <label class="mm-field"><span>上下文长度</span><input v-model.number="modelForm.context_length" class="ctrl" type="number" min="1024" /></label>
        <label class="mm-field"><span>最大输出 tokens</span><input v-model.number="modelForm.max_output_tokens" class="ctrl" type="number" min="256" /></label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>描述</span><input v-model="modelForm.description" class="ctrl" placeholder="用途与能力说明" /></label>
      </div>
      <div class="form-title small">价格（USD / 每 1M tokens，0 表示免费/未知）</div>
      <div class="form-row">
        <label class="mm-field"><span>输入</span><input v-model.number="modelForm.input_price" class="ctrl" type="number" min="0" step="0.0001" /></label>
        <label class="mm-field"><span>输出</span><input v-model.number="modelForm.output_price" class="ctrl" type="number" min="0" step="0.0001" /></label>
        <label class="mm-field"><span>缓存命中（读）</span><input v-model.number="modelForm.cache_read_price" class="ctrl" type="number" min="0" step="0.0001" /></label>
        <label class="mm-field"><span>缓存写入</span><input v-model.number="modelForm.cache_write_price" class="ctrl" type="number" min="0" step="0.0001" /></label>
      </div>
      <div class="form-row">
        <label class="mm-check"><input v-model="modelForm.tool_use" type="checkbox" /> 支持工具调用</label>
        <label class="mm-check"><input v-model="modelForm.vision" type="checkbox" /> 支持视觉</label>
        <label class="mm-check"><input v-model="modelForm.reasoning" type="checkbox" /> 推理模型</label>
      </div>
      <div class="form-row">
        <label class="mm-field"><span>思考模式（Ollama）</span>
          <select v-model="modelForm.thinking" class="ctrl">
            <option value="">跟随后端默认（关闭）</option>
            <option value="disabled">关闭（推荐）</option>
            <option value="enabled">开启思考</option>
            <option value="adaptive">自适应（服务端默认）</option>
          </select>
        </label>
      </div>
      <p class="hint">开启思考时 Ollama 只输出 reasoning_content、正文为空（会拖累工具循环），非必要勿开；仅对 Ollama 模型生效。</p>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>variants（JSON，如 {"fast": {"settings": {"think": false}}}）</span>
          <textarea v-model="modelForm.variants" class="ctrl" rows="3" placeholder='{"fast": {"settings": {}, "headers": {}, "body": {}}}' />
        </label>
      </div>
      <div class="form-footer">
        <button class="btn" :disabled="mm.saving" @click="showModelForm = false">取消</button>
        <button class="btn btn-primary mm-save-btn" :disabled="mm.saving" @click="submitModel">
          <span v-if="mm.saving" class="spinner-mm"></span>
          {{ mm.saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.loading-wrap { display: flex; justify-content: center; padding: 48px; }
.mm-wrap { display: flex; flex-direction: column; gap: 20px; animation: fadeSlideUp 0.4s var(--ease); }
.mm-block { padding: 18px; }
.mm-block .form-title { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.form-title.small { margin-top: 8px; }
/* ── 操作结果 toast（固定右上，覆盖弹窗遮罩） ── */
.mm-toast {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 9999;
  padding: 10px 16px;
  border-radius: var(--radius);
  font-size: 13px;
  font-weight: 600;
  box-shadow: var(--shadow-lg, 0 10px 40px rgba(0, 0, 0, 0.25));
}
.mm-toast--ok {
  background: color-mix(in srgb, var(--primary) 14%, var(--surface));
  color: var(--primary);
  border: 1px solid color-mix(in srgb, var(--primary) 32%, transparent);
}
.mm-toast--err {
  background: color-mix(in srgb, var(--danger) 14%, var(--surface));
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 32%, transparent);
}
.toast-enter-active, .toast-leave-active { transition: opacity 0.25s var(--ease), transform 0.25s var(--ease); }
.toast-enter-from { opacity: 0; transform: translateY(-10px); }
.toast-leave-to { opacity: 0; transform: translateY(-6px); }
.mm-field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 180px; }
.mm-field > span { font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.03em; }
.mm-field.mm-wide { flex: 2; }
.provider-prefix-hint { margin: 0 0 2px; }
.preset-table .mm-th, .preset-table .mm-tr { grid-template-columns: 1.4fr 1.4fr 0.8fr 2.2fr 1fr; min-width: 720px; }
.preset-table small { font-size: 10px; }

/* ── 默认/轻量模型选择：与聊天框模型胶囊同款样式（横向排列） ── */
.mm-defaults-row { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }
.mm-pill-field { display: flex; align-items: center; gap: 8px; }
.mm-pill-caption { font-size: 12px; font-weight: 600; color: var(--text-secondary); white-space: nowrap; }
.mm-defaults-hint { margin: 8px 0 0; }
.mm-title-action { margin-left: auto; }
.mm-title-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.mm-save-btn { display: inline-flex; align-items: center; gap: 6px; }
.mm-save-btn:disabled { cursor: not-allowed; }
.spinner-mm {
  width: 12px;
  height: 12px;
  border: 2px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
.mm-refresh-spin { margin-left: 8px; }

/* ── 弹窗表单控件：与应用其余页面统一（CustomToolsView .ctrl 同款） ── */
.ctrl {
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: all var(--duration) var(--ease);
  width: 100%;
  box-sizing: border-box;
}
.ctrl:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}
.mm-field .ctrl { width: 100%; }
.form-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  justify-content: flex-end;
}
.form-footer label { margin-right: auto; }
.mm-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 30px;
  min-width: 150px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--text-secondary, #64748b) 8%, var(--bg));
  border: 1px solid color-mix(in srgb, var(--text-secondary, #64748b) 16%, var(--border));
  transition: border-color 0.15s, background 0.15s;
}
.mm-pill:hover,
.mm-pill:focus-within {
  border-color: var(--primary, #4f46e5);
  background: color-mix(in srgb, var(--primary, #4f46e5) 6%, var(--bg));
}
.mm-pill-select {
  appearance: none;
  -webkit-appearance: none;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  height: 100%;
  width: 100%;
  padding: 0 26px 0 26px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mm-pill-icon {
  position: absolute;
  left: 10px;
  color: var(--primary, #4f46e5);
  pointer-events: none;
  flex-shrink: 0;
}
.mm-pill-icon-two { color: #f59e0b; }
.mm-pill-chevron {
  position: absolute;
  right: 8px;
  color: var(--text-secondary);
  pointer-events: none;
  flex-shrink: 0;
  transition: transform 0.2s;
}
.mm-pill:focus-within .mm-pill-chevron { transform: rotate(180deg); }
.mm-defaults-save { display: flex; align-items: flex-end; padding-bottom: 2px; }
.mm-check { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; margin-right: 14px; }
.hint { font-size: 12px; color: var(--text-secondary); margin: 2px 0 10px; }
.hint code { font-family: 'JetBrains Mono', Consolas, monospace; }
.empty-tip { padding: 18px; text-align: center; color: var(--text-tertiary, #9aa); font-size: 13px; }
.mm-table { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; overflow-x: auto; }
.mm-th, .mm-tr { display: grid; grid-template-columns: 1.4fr 2fr 0.7fr 1.1fr 0.9fr 1fr; gap: 10px; align-items: center; font-size: 12px; padding: 8px 12px; min-width: 760px; }
.provider-table .mm-th, .provider-table .mm-tr { grid-template-columns: 1.2fr 2.2fr 0.6fr 0.7fr 0.9fr; min-width: 640px; }
.mm-th { color: var(--text-secondary); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.mm-tr { background: var(--bg-subtle); border-radius: var(--radius); border: 1px solid var(--border-subtle); color: var(--text); }
.mm-tr:hover { background: var(--surface); border-color: color-mix(in srgb, var(--primary) 20%, var(--border)); }
.mm-main { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mm-id { color: var(--text-secondary); }
.mm-mono { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mm-num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.mm-cap { color: var(--text-secondary); }
.mm-ops { display: flex; gap: 6px; justify-content: flex-end; }

/* ── 模型列表：检索 + 分页 ── */
.mm-list-toolbar { display: flex; align-items: center; gap: 12px; margin: 6px 0 4px; flex-wrap: wrap; }
.mm-search {
  display: flex; align-items: center; gap: 6px; flex: 1; max-width: 320px;
  padding: 7px 12px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); color: var(--text-secondary);
  transition: all var(--duration) var(--ease);
}
.mm-search:focus-within { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.mm-search input { flex: 1; border: none; background: transparent; color: var(--text); font-size: 13px; outline: none; }
.mm-count { font-size: 12px; color: var(--text-secondary); }
.mm-pager { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.mm-pager-info { font-size: 12px; color: var(--text-secondary); margin-right: auto; }
.mm-pagesize { font-size: 12px; color: var(--text-secondary); display: inline-flex; align-items: center; gap: 6px; }
.mm-pagesize select { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font-size: 12px; }
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: flex; align-items: flex-start; justify-content: center; padding: 6vh 16px; z-index: 200; overflow-y: auto; }
.dialog { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg, 14px); padding: 20px; max-width: 520px; width: 100%; box-shadow: var(--shadow-lg, 0 10px 40px rgba(0,0,0,0.25)); display: flex; flex-direction: column; gap: 12px; }
.dialog.wide { max-width: 720px; }
.switch { position: relative; display: inline-block; width: 38px; height: 20px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; inset: 0; background: var(--border); border-radius: 20px; transition: 0.2s; cursor: pointer; }
.slider::before { content: ''; position: absolute; width: 16px; height: 16px; left: 2px; top: 2px; background: #fff; border-radius: 50%; transition: 0.2s; }
.switch input:checked + .slider { background: var(--primary); }
.switch input:checked + .slider::before { transform: translateX(18px); }

/* ── 本地模型下载 ── */
.lm-source { width: auto; min-width: 150px; }
.lm-error { color: var(--danger); font-size: 12px; margin: 6px 0 0; }
.lm-sub {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-secondary);
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
}
.lm-more { display: flex; align-items: center; gap: 12px; margin-top: 10px; flex-wrap: wrap; }
.lm-more-btn { display: inline-flex; align-items: center; gap: 6px; }
.lm-table .mm-th, .lm-table .mm-tr { grid-template-columns: 2.4fr 0.7fr 0.8fr 0.9fr; min-width: 620px; }
.lm-file-table .mm-th, .lm-file-table .mm-tr { grid-template-columns: 2.6fr 0.8fr 0.7fr 0.5fr; min-width: 620px; }
.lm-file-row { cursor: pointer; }
.lm-row-active {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 7%, var(--bg-subtle));
}
.lm-files { margin-top: 12px; border-top: 1px dashed var(--border); padding-top: 10px; }
.lm-files-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; font-size: 13px; }
.lm-actions { display: flex; align-items: center; gap: 12px; margin-top: 10px; flex-wrap: wrap; }
.lm-jobs { margin-top: 14px; border-top: 1px dashed var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.lm-job { background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: var(--radius); padding: 10px 12px; }
.lm-job-main { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.lm-job-state {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--text-secondary) 12%, transparent);
  color: var(--text-secondary);
}
.lm-state-done { color: var(--primary); background: color-mix(in srgb, var(--primary) 14%, transparent); }
.lm-state-error { color: var(--danger); background: color-mix(in srgb, var(--danger) 14%, transparent); }
.lm-state-cancelled { color: var(--text-tertiary, #9aa); }
.lm-bar { height: 6px; border-radius: 999px; background: var(--border); overflow: hidden; margin: 8px 0 6px; }
.lm-bar-fill { height: 100%; background: var(--primary); transition: width 0.4s var(--ease); }
.lm-bar-done { background: var(--success, #22c55e); }
.lm-job-foot { display: flex; align-items: center; gap: 10px; }
.lm-job-foot .mm-ops { margin-left: auto; }
.lm-job-error { color: var(--danger); font-size: 12px; }
</style>

<style>
/* ── 模型选择下拉：原生弹层跟随主题 + 个人背景色 ──
 * scoped 样式无法命中 <select> 展开时的 UA 弹层，这里用全局选择器补上
 * color-scheme 与 option 配色，使弹层 (Chrome/Edge 深色下) 不再白底。 */
html[data-theme='dark'] .mm-pill-select { color-scheme: dark; }
html:not([data-theme='dark']) .mm-pill-select { color-scheme: light; }
.mm-pill-select option {
  background-color: var(--bg);
  color: var(--text);
}
.mm-pill-select option:hover {
  background-color: var(--bg-subtle);
}
</style>