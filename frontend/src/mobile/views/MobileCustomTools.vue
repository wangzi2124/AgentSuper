<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useCustomToolsStore } from '../../stores/customTools'
import type { CustomToolItem } from '../../types/customTools'
import { showConfirmDialog, showToast } from 'vant'

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
  try { await store.fetchCatalog() } catch (e: any) { console.warn('加载工具目录失败:', e?.message || e) }
})

// ── 创建脚本型 ──
const showScriptForm = ref(false)
const creating = ref(false)
const scriptForm = ref({ name: '', description: '', script: '', enabled: true })

async function handleCreateScript() {
  if (!scriptForm.value.name.trim() || !scriptForm.value.script.trim()) {
    showToast('请填写工具名与 Python 源码（需包含 tool_* 函数）')
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
    showToast('创建成功')
  } catch (err: any) {
    showToast(err?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

// ── 固定型 ──
const showPinForm = ref(false)
const pinForm = ref({ tool_name: '', description: '' })
const pinnedNames = computed(() => new Set(store.items.filter((i) => i.type === 'pin').map((i) => i.name)))
const pinCandidates = computed(() => store.catalog.filter((t) => !pinnedNames.value.has(t.name)))

async function handlePin() {
  if (!pinForm.value.tool_name) { showToast('请选择要固定的工具'); return }
  try {
    await store.pin({ tool_name: pinForm.value.tool_name, description: pinForm.value.description.trim() })
    pinForm.value = { tool_name: '', description: '' }
    showPinForm.value = false
    showToast('已固定')
  } catch (err: any) {
    showToast(err?.message || '固定失败')
  }
}

// ── 切换 / 删除 ──
async function handleToggle(item: CustomToolItem, enabled: boolean) {
  try {
    await store.toggle(item.name, !enabled)
  } catch (err: any) {
    showToast(err.message)
  }
}

async function handleDelete(item: CustomToolItem) {
  try {
    await showConfirmDialog({ title: '删除工具', message: `确认删除自定义工具「${item.name}」？` })
    await store.remove(item.name)
    showToast('已删除')
  } catch (err: any) {
    if (err?.message && !String(err.message).includes('cancel')) showToast(err.message)
  }
}
</script>

<template>
  <div class="m-tools">
    <van-search v-model="searchText" placeholder="搜索工具名称或描述..." />

    <div class="cta">
      <van-button size="small" plain type="primary" icon="plus" @click="showScriptForm = true">创建脚本工具</van-button>
      <van-button size="small" plain type="primary" icon="bookmark-o" @click="showPinForm = true">固定已有工具</van-button>
    </div>

    <van-loading v-if="store.loading" class="loading" />
    <van-empty v-else-if="store.items.length === 0" image="search" description="暂无自定义工具" />

    <div v-else class="tool-list">
      <div class="count">{{ filteredItems.length }} 个自定义工具</div>
      <van-swipe-cell v-for="item in filteredItems" :key="item.type + ':' + item.name">
        <van-cell
          :title="item.name"
          :label="`${item.type === 'script' ? '🧩' : '📌'} ${item.description || ''} · ${item.tools.join(', ')}`"
          center
        >
          <template #right-icon>
            <van-switch :model-value="item.enabled" size="20px" @update:model-value="handleToggle(item, item.enabled)" />
          </template>
        </van-cell>
        <template #right>
          <div class="swipe-del" @click="handleDelete(item)">删除</div>
        </template>
      </van-swipe-cell>
    </div>

    <van-popup v-model:show="showScriptForm" round position="bottom" :style="{ height: '80%' }">
      <div class="form-panel">
        <div class="form-title">创建脚本型工具</div>
        <van-field v-model="scriptForm.name" label="工具名" placeholder="如 weather_plus" />
        <van-field v-model="scriptForm.description" label="描述" placeholder="可选" />
        <van-field
          v-model="scriptForm.script"
          label="源码"
          type="textarea"
          rows="8"
          autosize
          placeholder="粘贴 Python 源码（需包含 tool_* 函数）"
        />
        <div class="form-row">
          <van-switch v-model="scriptForm.enabled" size="20px" />
          <span class="row-label">创建后启用</span>
          <div style="flex:1"></div>
          <van-button type="primary" size="small" :loading="creating" @click="handleCreateScript">创建并热加载</van-button>
        </div>
      </div>
    </van-popup>

    <van-popup v-model:show="showPinForm" round position="bottom" :style="{ height: '70%' }">
      <div class="form-panel">
        <div class="form-title">固定已有工具</div>
        <van-field v-model="pinForm.description" label="备注" placeholder="选填" />
        <div class="pin-options">
          <van-cell
            v-for="t in pinCandidates"
            :key="t.name"
            :title="t.name"
            :label="t.description || ''"
            clickable
            :class="{ active: pinForm.tool_name === t.name }"
            @click="pinForm.tool_name = t.name"
          />
          <van-empty v-if="pinCandidates.length === 0" description="没有可固定的工具" />
        </div>
        <div class="form-row">
          <div style="flex:1"></div>
          <van-button type="primary" size="small" @click="handlePin">固定</van-button>
        </div>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.m-tools { padding-bottom: 16px; }
.cta { display: flex; gap: 12px; padding: 4px 16px 12px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.tool-list { display: flex; flex-direction: column; }
.count { font-size: 12px; color: #97a0b4; padding: 6px 16px; }
.swipe-del { width: 64px; height: 100%; display: flex; align-items: center; justify-content: center; background: #ee0a24; color: #fff; font-size: 14px; }
.form-panel { padding: 16px; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; }
.form-title { font-size: 16px; font-weight: 600; padding: 4px 8px 16px; }
.form-panel :deep(textarea.van-field__control) { min-height: 140px; }
.form-row { display: flex; align-items: center; gap: 8px; padding: 16px 8px; }
.row-label { font-size: 14px; color: var(--text); }
.pin-options { flex: 1; overflow-y: auto; }
.pin-options :deep(.van-cell.active) { background: var(--primary-soft); }
</style>
