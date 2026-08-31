<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useCustomToolsStore } from '../stores/customTools'
import type { CustomToolItem } from '../types/customTools'

const store = useCustomToolsStore()

const searchText = ref('')
const filteredItems = computed(() => {
  const q = searchText.value.toLowerCase().trim()
  if (!q) return store.items
  return store.items.filter(
    (item) => item.name.toLowerCase().includes(q) || (item.description && item.description.toLowerCase().includes(q))
  )
})

onMounted(async () => {
  await store.fetchAll()
  try {
    await store.fetchCatalog()
  } catch (err: any) {
    console.warn('加载工具目录失败:', err?.message || err)
  }
})

const scriptForm = ref({ name: '', description: '', script: '', enabled: true })
const showScriptForm = ref(false)
const creating = ref(false)
const scriptError = ref('')

async function handleCreateScript() {
  scriptError.value = ''
  if (!scriptForm.value.name.trim() || !scriptForm.value.script.trim()) {
    scriptError.value = '请填写工具名与 Python 源码（需包含 tool_* 函数）'
    return
  }
  creating.value = true
  try {
    await store.createScript({
      name: scriptForm.value.name.trim(),
      description: scriptForm.value.description.trim(),
      script: scriptForm.value.script,
      enabled: scriptForm.value.enabled,
    })
    scriptForm.value = { name: '', description: '', script: '', enabled: true }
    showScriptForm.value = false
  } catch (err: any) {
    scriptError.value = err?.message || '创建失败'
  } finally {
    creating.value = false
  }
}

const pinForm = ref({ tool_name: '', description: '' })
const pinError = ref('')

async function handlePin() {
  pinError.value = ''
  if (!pinForm.value.tool_name) {
    pinError.value = '请选择要固定的工具'
    return
  }
  try {
    await store.pin({
      tool_name: pinForm.value.tool_name,
      description: pinForm.value.description.trim(),
    })
    pinForm.value = { tool_name: '', description: '' }
  } catch (err: any) {
    pinError.value = err?.message || '固定失败'
  }
}

async function handleToggle(item: CustomToolItem, enabled: boolean) {
  try {
    await store.toggle(item.name, !enabled)
  } catch (err: any) {
    alert(err.message)
  }
}

async function handleDelete(item: CustomToolItem) {
  if (!confirm(`确认删除自定义工具「${item.name}」？`)) return
  try {
    await store.remove(item.name)
  } catch (err: any) {
    alert(err.message)
  }
}

const pinnedNames = () => new Set(store.items.filter((i) => i.type === 'pin').map((i) => i.name))
const pinCandidates = () => {
  const q = searchText.value.toLowerCase().trim()
  const candidates = store.catalog.filter((t) => !pinnedNames().has(t.name))
  if (!q) return candidates
  return candidates.filter(
    (t) => t.name.toLowerCase().includes(q) || (t.description && t.description.toLowerCase().includes(q))
  )
}
</script>

<template>
  <div class="page-header">
    <h2>自定义工具</h2>
    <p>创建脚本工具或固定已有工具，使其 schema 始终挂载（token 优化 v6）</p>
  </div>
  <div class="page-content tools-wrap">
    <!-- 创建入口 -->
    <div class="card create-bar">
      <button class="btn btn-primary" @click="showScriptForm = !showScriptForm">
        {{ showScriptForm ? '收起' : '+' }} 创建脚本工具
      </button>
      <div class="pin-group">
        <input
          v-model="searchText"
          placeholder="搜索工具名称或描述..."
          class="ctrl"
        />
        <select
          v-model="pinForm.tool_name"
          class="ctrl"
          title="选择已有工具固定"
        >
          <option value="" disabled>选择已有工具固定</option>
          <option v-for="t in pinCandidates()" :key="t.name" :value="t.name" :title="t.name + ' — ' + (t.description || '无描述')">
            {{ t.name.length > 12 ? t.name.slice(0, 12) + '…' : t.name }}{{ t.description ? ' — ' + (t.description.length > 16 ? t.description.slice(0, 16) + '…' : t.description) : '' }}
          </option>
        </select>
      </div>
      <input
        v-model="pinForm.description"
        placeholder="固定备注（可选）"
        class="ctrl desc-input"
      />
      <button class="btn btn-primary" @click="handlePin">固定</button>
    </div>

    <!-- 脚本型创建表单 -->
    <div v-if="showScriptForm" class="card script-form">
      <div class="form-title">创建脚本型工具（写入 plugins/custom_*.py 并热加载）</div>
      <div class="form-row">
        <input
          v-model="scriptForm.name"
          placeholder="工具名（如 weather_plus）"
          class="ctrl"
        />
        <input
          v-model="scriptForm.description"
          placeholder="描述（可选）"
          class="ctrl"
        />
      </div>
      <textarea
        v-model="scriptForm.script"
        placeholder="粘贴 Python 源码（需包含 tool_* 函数）&#10;def tool_echo(msg: str) -> str:&#10;    return 'echo: ' + msg"
        rows="10"
        class="ctrl code-area"
      ></textarea>
      <div class="form-footer">
        <label class="check-label">
          <input v-model="scriptForm.enabled" type="checkbox" /> 创建后启用
        </label>
        <button class="btn btn-primary" :disabled="creating" @click="handleCreateScript">
          {{ creating ? '创建中…' : '创建并热加载' }}
        </button>
        <span v-if="scriptError" class="form-error">{{ scriptError }}</span>
      </div>
    </div>

    <span v-if="pinError" class="form-error">{{ pinError }}</span>

    <!-- 列表 -->
    <div v-if="store.loading" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="store.items.length === 0" class="empty-state">
      <div class="icon">🧰</div>
      <p>暂无自定义工具</p>
      <p style="font-size:13px;margin-top:4px;">创建脚本工具或固定已有工具，使其 schema 始终挂载。</p>
    </div>

    <div v-else class="tool-list">
      <div class="list-meta">
        {{ filteredItems.length }} 个自定义工具
      </div>
      <div v-for="item in filteredItems" :key="item.type + ':' + item.name" class="card tool-card">
        <div class="tool-icon">{{ item.type === 'script' ? '🧩' : '📌' }}</div>
        <div class="tool-info">
          <div class="tool-title">
            {{ item.name }}
            <span class="badge type-badge">{{ item.type === 'script' ? 'script' : 'pin' }}</span>
          </div>
          <div class="tool-desc">{{ item.description }}</div>
          <div class="tool-sub">
            <span>{{ item.tools.join(', ') }}</span>
            <span class="path">{{ item.path }}</span>
          </div>
        </div>
        <span class="badge" :class="item.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ item.enabled ? '已启用' : '已禁用' }}
        </span>
        <div class="tool-actions">
          <button
            class="btn btn-sm"
            :class="item.enabled ? 'btn-danger' : 'btn-primary'"
            @click="handleToggle(item, item.enabled)"
          >
            {{ item.enabled ? '禁用' : '启用' }}
          </button>
          <button class="btn btn-sm btn-danger" @click="handleDelete(item)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tools-wrap { display: flex; flex-direction: column; gap: 16px; }
.loading-wrap { display: flex; justify-content: center; padding: 48px; }

.create-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.pin-group { display: flex; gap: 8px; flex: 1; min-width: 200px; max-width: 420px; }
.pin-group .ctrl { flex: 1; min-width: 0; }
.desc-input { flex: 1; min-width: 160px; max-width: 240px; }

.ctrl {
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: all var(--duration) var(--ease);
}
.ctrl:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}

.script-form { display: flex; flex-direction: column; gap: 12px; animation: fadeSlideUp 0.3s var(--ease); }
.form-title { font-weight: 700; font-size: 14px; color: var(--text); }
.form-row { display: flex; gap: 10px; flex-wrap: wrap; }
.form-row .ctrl { flex: 1; min-width: 180px; }
.code-area {
  width: 100%;
  box-sizing: border-box;
  background: var(--bg-subtle);
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  resize: vertical;
  line-height: 1.6;
}
.form-footer { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.check-label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text); }
.form-error { font-size: 12px; color: var(--danger); }

.tool-list { display: flex; flex-direction: column; gap: 10px; animation: fadeSlideUp 0.4s var(--ease); }
.list-meta { font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; font-weight: 500; }

.tool-card {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  border-radius: var(--radius-lg);
  transition: all var(--duration) var(--ease);
}
.tool-card:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}
.tool-icon {
  font-size: 24px;
  width: 48px;
  height: 48px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
}
.tool-info { flex: 1; min-width: 0; }
.tool-title { font-weight: 700; font-size: 14px; color: var(--text); display: flex; align-items: center; gap: 8px; }
.type-badge {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.tool-desc { font-size: 12.5px; color: var(--text-secondary); margin-top: 3px; line-height: 1.5; }
.tool-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; display: flex; flex-direction: column; gap: 2px; }
.tool-sub .path { font-family: 'JetBrains Mono', Consolas, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-actions { display: flex; gap: 8px; flex-shrink: 0; }
.btn-sm { padding: 7px 14px; font-size: 12px; }

@media (max-width: 600px) {
  .tool-card { gap: 12px; }
  .tool-actions { margin-left: auto; }
}
</style>
