<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { browseDirectories } from '../api/permission'
import { baseName, loadRecentPaths, rememberRecentPath } from '../utils/workspaceNav'

const props = defineProps<{ show: boolean }>()

const emit = defineEmits<{ close: []; select: [path: string] }>()

const currentPath = ref('')
const currentName = ref('')
const parentPath = ref('')
const dirs = ref<Array<{ name: string; path: string }>>([])
const loading = ref(false)
const error = ref('')
const pathValue = ref('')
const pathInput = ref<HTMLInputElement | null>(null)
const activeIndex = ref(-1)
const recentPaths = ref<string[]>(loadRecentPaths())
const dirLabel = baseName

async function load(path: string) {
  loading.value = true
  error.value = ''
  try {
    const data = await browseDirectories(path)
    currentPath.value = data.path
    currentName.value = data.name
    parentPath.value = data.parent
    dirs.value = data.dirs
    pathValue.value = data.path
    activeIndex.value = -1
  } catch (e: any) {
    error.value = e?.message || '加载目录失败'
    dirs.value = []
  } finally {
    loading.value = false
  }
}

function openDir(dir: { path: string }) {
  void load(dir.path)
}

async function openTypedPath() {
  await load(pathValue.value.trim())
}

function goUp() {
  if (parentPath.value) void load(parentPath.value)
  else void load('')
}

function selectRecent(path: string) {
  rememberRecentPath(path)
  emit('select', path)
  emit('close')
}

function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  const inInput = target instanceof HTMLInputElement
  if (event.key === 'Enter') {
    event.preventDefault()
    if (inInput) void openTypedPath()
    else if (target instanceof HTMLButtonElement && target.classList.contains('dir-quick-go')) void openTypedPath()
    else if (target instanceof HTMLButtonElement && target.classList.contains('dir-btn-secondary')) cancel()
    else if (target instanceof HTMLButtonElement && target.classList.contains('dir-btn-primary')) confirm()
    else if (activeIndex.value >= 0 && dirs.value[activeIndex.value]) openDir(dirs.value[activeIndex.value])
    else confirm()
    return
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!dirs.value.length) return
    const delta = event.key === 'ArrowDown' ? 1 : -1
    activeIndex.value = (activeIndex.value + delta + dirs.value.length) % dirs.value.length
    return
  }
  if (event.key === 'Backspace' && !inInput) {
    event.preventDefault()
    goUp()
  }
}

watch(() => props.show, async (show) => {
  if (!show) return
  currentPath.value = ''
  currentName.value = ''
  parentPath.value = ''
  dirs.value = []
  error.value = ''
  pathValue.value = ''
  activeIndex.value = -1
  recentPaths.value = loadRecentPaths()
  await load('')
  await nextTick()
  pathInput.value?.focus()
})

function confirm() {
  if (!currentPath.value) return
  recentPaths.value = rememberRecentPath(currentPath.value)
  emit('select', currentPath.value)
  emit('close')
}

function cancel() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="dir-modal-overlay" @click.self="cancel">
      <div class="dir-modal" tabindex="-1" @keydown="onKeydown">
      <div class="dir-modal-header">
        <span class="dir-modal-title">选择目录</span>
        <button class="dir-modal-close" @click="cancel" title="关闭">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>

      <div class="dir-quick">
        <span class="dir-quick-label">路径</span>
        <input ref="pathInput" v-model="pathValue" placeholder="粘贴绝对路径，回车打开" aria-label="目录路径" />
        <button class="dir-quick-go" @click="openTypedPath">打开</button>
      </div>

      <div class="dir-path">
        <button v-if="currentPath" class="dir-up" @click="goUp" title="返回上级/盘符列表">↑</button>
        <span class="dir-path-text" :title="currentPath">{{ currentPath || '请选择盘符/目录' }}</span>
        <span v-if="currentName" class="dir-path-name">{{ currentName }}</span>
      </div>

      <div v-if="recentPaths.length" class="dir-recents">
        <span class="dir-recents-label">最近</span>
        <button v-for="p in recentPaths.slice(0, 4)" :key="p" class="dir-recent" :title="p" @click="selectRecent(p)">{{ dirLabel(p) }}</button>
      </div>

      <p v-if="error" class="dir-error">{{ error }}</p>
      <p v-else-if="loading" class="dir-loading">加载中...</p>

      <div v-else class="dir-list">
        <div v-if="dirs.length === 0" class="dir-empty">该目录下没有子目录</div>
        <div
          v-for="(d, i) in dirs"
          :key="d.path"
          class="dir-item"
          :class="{ active: activeIndex === i }"
          :title="d.path"
          @click="openDir(d)"
          @mouseenter="activeIndex = i"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span class="dir-item-name">{{ d.name }}</span>
          <span class="dir-item-open">›</span>
        </div>
      </div>

      <div class="dir-modal-footer">
        <span class="dir-hint">↑↓ 选择 · Enter 打开</span>
        <button class="dir-btn dir-btn-secondary" @click="cancel">取消</button>
        <button class="dir-btn dir-btn-primary" :disabled="!currentPath" @click="confirm">选择此目录</button>
      </div>
      </div>
    </div>
  </Teleport>
</template>


<style scoped src="../styles/chat/dirPickerModal.css"></style>
