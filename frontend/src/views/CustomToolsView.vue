<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useCustomToolsStore } from '../stores/customTools'
import type { CustomToolItem } from '../types/customTools'

const store = useCustomToolsStore()

// ── 搜索 ──
const searchText = ref('')
const filteredItems = computed(() => {
  const q = searchText.value.toLowerCase().trim()
  if (!q) return store.items
  return store.items.filter(
    (item) => item.name.toLowerCase().includes(q) || (item.description && item.description.toLowerCase().includes(q))
  )
})

// ── 列表 ──
onMounted(async () => {
  await store.fetchAll()
  try {
    await store.fetchCatalog()
  } catch (err: any) {
    console.warn('加载工具目录失败:', err?.message || err)
  }
})

// ── 脚本型创建表单 ──
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

// ── 固定型表单 ──
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

// ── 切换 / 删除 ──
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

// 已固定（pin）的工具：从目录中移除，避免重复固定
const pinnedNames = () => new Set(store.items.filter((i) => i.type === 'pin').map((i) => i.name))
const pinCandidates = () => {
  const q = searchText.value.toLowerCase().trim()
  const candidates = store.catalog.filter((t) => !pinnedNames().has(t.name))
  if (!q) return candidates
  return candidates.filter(
    (t) => t.name.toLowerCase().includes(q) || (t.description && t.description.toLowerCase().includes(q))
  )
}

const catalogDesc = (name: string) => {
  const t = store.catalog.find((c) => c.name === name)
  return t?.description || ''
}
</script>

<template>
  <div class="page-header">
    <h2>自定义工具</h2>
    <p>创建脚本工具或固定已有工具，使其 schema 始终挂载（token 优化 v6）</p>
  </div>
  <div class="page-content" style="display:flex;flex-direction:column;gap:16px;">
    <!-- 创建入口 -->
    <div class="card" style="display:flex;flex-direction:column;gap:10px;">
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
        <button class="btn btn-primary" style="font-size:13px;" @click="showScriptForm = !showScriptForm">
          {{ showScriptForm ? '收起' : '+' }} 创建脚本工具
        </button>
        <input
          v-model="searchText"
          placeholder="搜索工具名称或描述..."
          style="flex:1;min-width:160px;max-width:260px;padding:8px 12px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);font-size:13px;"
        />
        <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:200px;">
          <select
            v-model="pinForm.tool_name"
            :title="catalogDesc(pinForm.tool_name)"
            style="flex:1;max-width:300px;padding:8px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);font-size:13px;min-width:0;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
          >
            <option value="" disabled>选择已有工具固定</option>
            <option v-for="t in pinCandidates()" :key="t.name" :value="t.name">
              {{ t.name }}
            </option>
          </select>
          <input
            v-model="pinForm.description"
            placeholder="固定备注（可选）"
            style="flex:1;min-width:120px;max-width:200px;padding:8px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);font-size:13px;"
          />
          <button class="btn btn-primary" style="font-size:13px;white-space:nowrap;" @click="handlePin">固定</button>
        </div>
      </div>
      <div
        v-if="pinForm.tool_name && catalogDesc(pinForm.tool_name)"
        style="font-size:12px;color:var(--text-secondary);padding:4px 8px;background:var(--bg);border-radius:var(--radius);line-height:1.5;"
      >
        {{ catalogDesc(pinForm.tool_name) }}
      </div>
    </div>

    <!-- 脚本型创建表单 -->
    <div v-if="showScriptForm" class="card" style="display:flex;flex-direction:column;gap:10px;">
      <div style="font-weight:600;font-size:14px;">创建脚本型工具（写入 plugins/custom_*.py 并热加载）</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <input
          v-model="scriptForm.name"
          placeholder="工具名（如 weather_plus）"
          style="flex:1;min-width:180px;padding:8px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);"
        />
        <input
          v-model="scriptForm.description"
          placeholder="描述（可选）"
          style="flex:1;min-width:180px;padding:8px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);"
        />
      </div>
      <textarea
        v-model="scriptForm.script"
        placeholder="粘贴 Python 源码（需包含 tool_* 函数）&#10;def tool_echo(msg: str) -> str:&#10;    return 'echo: ' + msg"
        rows="10"
        style="width:100%;padding:10px;border-radius:var(--radius);border:1px solid var(--border);background:var(--bg);font-family:monospace;font-size:12px;resize:vertical;box-sizing:border-box;"
      ></textarea>
      <div style="display:flex;align-items:center;gap:10px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
          <input v-model="scriptForm.enabled" type="checkbox" /> 创建后启用
        </label>
        <button class="btn btn-primary" style="font-size:13px;" :disabled="creating" @click="handleCreateScript">
          {{ creating ? '创建中…' : '创建并热加载' }}
        </button>
        <span v-if="scriptError" style="font-size:12px;color:var(--danger);">{{ scriptError }}</span>
      </div>
    </div>

    <span v-if="pinError" style="font-size:12px;color:var(--danger);">{{ pinError }}</span>

    <!-- 列表 -->
    <div v-if="store.loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="store.items.length === 0" class="empty-state">
      <div class="icon">🧰</div>
      <p>暂无自定义工具</p>
      <p style="font-size:13px;margin-top:4px;">创建脚本工具或固定已有工具，使其 schema 始终挂载。</p>
    </div>

    <div v-else style="display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
        {{ filteredItems.length }} 个自定义工具
      </div>
      <div v-for="item in filteredItems" :key="item.type + ':' + item.name" class="card" style="display:flex;align-items:center;gap:14px;">
        <div style="font-size:24px;">{{ item.type === 'script' ? '🧩' : '📌' }}</div>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:14px;">
            {{ item.name }}
            <span class="badge" style="margin-left:8px;font-size:10px;">{{ item.type === 'script' ? 'script' : 'pin' }}</span>
          </div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">{{ item.description }}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">{{ item.tools.join(', ') }}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">{{ item.path }}</div>
        </div>
        <span class="badge" :class="item.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ item.enabled ? '已启用' : '已禁用' }}
        </span>
        <button
          class="btn"
          :class="item.enabled ? 'btn-danger' : 'btn-primary'"
          style="font-size:12px;padding:6px 12px;"
          @click="handleToggle(item, item.enabled)"
        >
          {{ item.enabled ? '禁用' : '启用' }}
        </button>
        <button class="btn btn-danger" style="font-size:12px;padding:6px 12px;" @click="handleDelete(item)">删除</button>
      </div>
    </div>
  </div>
</template>
