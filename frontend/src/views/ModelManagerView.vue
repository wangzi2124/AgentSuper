<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useModelManagerStore } from '../stores/modelManager'
import type { ModelInfo, ProviderInfo } from '../api/models'

const mm = useModelManagerStore()

const defaultSel = ref<string>('')
const smallSel = ref<string>('')
const imageSel = ref<string>('')
const voiceSel = ref<string>('')

// [模型管理] 语音合成模型规格（Qwen3-TTS 尺寸）
const VOICE_SIZE_OPTIONS = ['0.6B', '1.7B']

onMounted(async () => {
  await mm.load()
  syncSelectors()
})

function syncSelectors() {
  defaultSel.value = mm.config?.default_model || ''
  smallSel.value = mm.config?.small_model || ''
  imageSel.value = mm.config?.image_caption_model || ''
  voiceSel.value = mm.config?.voice_model_size || ''
}

const modelList = computed(() => mm.config?.models || [])

// ── 模型列表：名称/ID 检索 + 分页 ──
const modelQuery = ref('')
const page = ref(1)
const pageSize = ref(10)

const filteredModels = computed(() => {
  const q = modelQuery.value.trim().toLowerCase()
  if (!q) return modelList.value
  return modelList.value.filter(m =>
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
const providerForm = reactive<{ name: string; label: string; api_base: string; api_key: string; enabled: boolean }>({
  name: '', label: '', api_base: '', api_key: '', enabled: true,
})
function openProviderForm(p?: ProviderInfo) {
  providerForm.name = p?.provider || ''
  providerForm.label = p?.label || ''
  providerForm.api_base = p?.api_base || ''
  providerForm.api_key = p?.api_key || ''
  providerForm.enabled = p?.enabled ?? true
  showProviderForm.value = true
}
async function submitProvider() {
  const name = providerForm.name.trim()
  if (!name || !providerForm.api_base.trim()) return
  if (name.includes('/')) {
    // Provider 名称是 litellm 前缀，不含 '/'；模型 id 形如 deepseek/deepseek-v4-flash
    mm.setError(`Provider 名称不能包含「/」，请只填前缀（如想让「deepseek/deepseek-v4-flash」可用，名称填 deepseek）`)
    return
  }
  await mm.updateProvider(name, { label: providerForm.label, api_base: providerForm.api_base, api_key: providerForm.api_key, enabled: providerForm.enabled })
  showProviderForm.value = false
}
async function toggleProvider(p: ProviderInfo) {
  await mm.updateProvider(p.provider, { enabled: !p.enabled })
}
async function delProvider(p: ProviderInfo) {
  if (!window.confirm(`删除 Provider「${p.label || p.provider}」？其自动注册的模型会一并消失。`)) return
  await mm.removeProvider(p.provider)
}

// ── 自定义模型编辑 ──
const showModelForm = ref(false)
const modelForm = reactive<{
  id: string; name: string; provider: string; family: string; description: string;
  context_length: number; max_output_tokens: number;
  input_price: number; output_price: number; cache_read_price: number; cache_write_price: number;
  tool_use: boolean; vision: boolean; reasoning: boolean;
}>({
  id: '', name: '', provider: 'ollama', family: '', description: '',
  context_length: 32768, max_output_tokens: 8192,
  input_price: 0, output_price: 0, cache_read_price: 0, cache_write_price: 0,
  tool_use: true, vision: false, reasoning: false,
})

function openModelForm(m?: ModelInfo) {
  modelForm.id = m?.id || ''
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
  showModelForm.value = true
}

async function submitModel() {
  if (!modelForm.id.trim()) return
  const entry: Partial<ModelInfo> & { id: string } = {
    id: modelForm.id.trim(),
    name: modelForm.name.trim() || modelForm.id.trim(),
    provider: modelForm.provider.trim() || (modelForm.id.split('/')[0] || ''),
    family: modelForm.family.trim() || modelForm.id.split('/')[1] || '',
    description: modelForm.description,
    context_length: Number(modelForm.context_length) || 32768,
    limits: { max_output_tokens: Number(modelForm.max_output_tokens) || 8192 },
    capabilities: { tool_use: modelForm.tool_use, vision: modelForm.vision, reasoning: modelForm.reasoning },
    cost: {
      input_per_1m: Number(modelForm.input_price) || 0,
      output_per_1m: Number(modelForm.output_price) || 0,
      cache_read_per_1m: Number(modelForm.cache_read_price) || 0,
      cache_write_per_1m: Number(modelForm.cache_write_price) || 0,
    },
  }
  await mm.addOrUpdateModel(entry)
  showModelForm.value = false
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
  return [c.tool_use && '工具', c.vision && '视觉', c.reasoning && '推理'].filter(Boolean).join('·') || '—'
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

      <!-- 模型列表 -->
      <section class="card mm-block">
        <div class="form-title">
          模型列表
          <span v-if="mm.refreshing" class="spinner-mm mm-refresh-spin" title="刷新中"></span>
          <button class="btn btn-primary mm-title-action" @click="openModelForm()">+ 自定义模型</button>
        </div>
        <p class="hint">自定义 <code>provider/model</code>：若该 provider 未配置，调用回落后端 .env 的 api_base/key。</p>
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
          <div v-if="!filteredModels.length" class="empty-tip">{{ modelQuery ? '未找到匹配的模型' : '暂无模型' }}</div>
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

  <!-- Provider 编辑弹窗 -->
  <div v-if="showProviderForm" class="overlay">
    <div class="dialog">
      <div class="form-title">{{ providerForm.name ? '编辑 Provider' : '新增 Provider' }}</div>
      <div class="form-row">
        <label class="mm-field"><span>名称（litellm 前缀）</span>
          <input v-model="providerForm.name" class="ctrl" placeholder="vllm" :disabled="!!providerForm.name" />
        </label>
        <label class="mm-field"><span>Label</span><input v-model="providerForm.label" class="ctrl" placeholder="本地 vLLM" /></label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>api_base（OpenAI 兼容）</span>
          <input v-model="providerForm.api_base" class="ctrl" placeholder="http://127.0.0.1:8001/v1" />
        </label>
      </div>
      <div class="form-row">
        <label class="mm-field mm-wide"><span>api_key（可选）</span>
          <input v-model="providerForm.api_key" class="ctrl" type="password" placeholder="sk-..." />
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
      <div class="form-title">{{ modelForm.id && modelList.find(m => m.id === modelForm.id) ? '编辑模型' : '新增自定义模型' }}</div>
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

/* ── 默认/轻量模型选择：与聊天框模型胶囊同款样式（横向排列） ── */
.mm-defaults-row { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }
.mm-pill-field { display: flex; align-items: center; gap: 8px; }
.mm-pill-caption { font-size: 12px; font-weight: 600; color: var(--text-secondary); white-space: nowrap; }
.mm-defaults-hint { margin: 8px 0 0; }
.mm-title-action { margin-left: auto; }
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