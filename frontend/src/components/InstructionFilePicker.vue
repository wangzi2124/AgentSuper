<script setup lang="ts">
import { ref, watch } from 'vue'
import { browseFiles, type BrowseEntry } from '../api/instructions'

const props = defineProps<{
  show: boolean
  startPath?: string
  busy?: boolean
}>()

const emit = defineEmits<{
  close: []
  select: [path: string]
}>()

const currentPath = ref('')
const currentName = ref('')
const parentPath = ref('')
const dirs = ref<BrowseEntry[]>([])
const files = ref<BrowseEntry[]>([])
const selected = ref('')
const loading = ref(false)
const error = ref('')

async function load(path: string) {
  loading.value = true
  error.value = ''
  selected.value = ''
  try {
    const data = await browseFiles(path)
    currentPath.value = data.path
    currentName.value = data.name
    parentPath.value = data.parent
    dirs.value = data.dirs || []
    files.value = data.files || []
  } catch (e) {
    error.value = String((e as Error).message || e)
    dirs.value = []
    files.value = []
  } finally {
    loading.value = false
  }
}

function openDir(dir: BrowseEntry) {
  load(dir.path)
}

function goUp() {
  load(parentPath.value)
}

function choose(file: BrowseEntry) {
  selected.value = file.path
}

function confirm() {
  if (selected.value) emit('select', selected.value)
}

watch(
  () => props.show,
  (visible) => {
    if (!visible) return
    currentPath.value = ''
    currentName.value = ''
    parentPath.value = ''
    dirs.value = []
    files.value = []
    selected.value = ''
    error.value = ''
    load(props.startPath || '')
  },
)
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="picker-overlay" @click.self="emit('close')">
      <div class="picker" role="dialog" aria-modal="true">
        <div class="picker-header">
          <span class="picker-title">选择指令文件</span>
          <button class="picker-close" type="button" title="关闭" @click="emit('close')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>

        <div class="picker-path">
          <button v-if="currentPath" class="picker-up" type="button" title="返回上级" @click="goUp">↑</button>
          <span class="picker-path-text" :title="currentPath">{{ currentPath || '请选择盘符/目录' }}</span>
        </div>

        <p v-if="error" class="picker-error">{{ error }}</p>
        <p v-else-if="loading" class="picker-loading">加载中…</p>

        <div v-else class="picker-list">
          <div v-if="!dirs.length && !files.length" class="picker-empty">该目录下没有子目录或文件</div>
          <button
            v-for="dir in dirs"
            :key="dir.path"
            class="picker-row"
            type="button"
            :title="dir.path"
            @click="openDir(dir)"
          >
            <span class="picker-icon">📁</span>
            <span class="picker-name">{{ dir.name }}</span>
            <span class="picker-go">›</span>
          </button>
          <button
            v-for="file in files"
            :key="file.path"
            class="picker-row picker-file"
            :class="{ active: selected === file.path }"
            type="button"
            :title="file.path"
            @dblclick="choose(file); confirm()"
            @click="choose(file)"
          >
            <span class="picker-icon">📄</span>
            <span class="picker-name">{{ file.name }}</span>
          </button>
        </div>

        <div class="picker-footer">
          <span class="picker-selected" :title="selected">{{ selected || '未选择文件' }}</span>
          <button class="btn" type="button" @click="emit('close')">取消</button>
          <button class="btn btn-primary" type="button" :disabled="!selected || busy" @click="confirm">添加</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.picker-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  animation: fadeIn 0.18s var(--ease);
}
.picker {
  width: min(560px, 100%);
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow-2xl);
  overflow: hidden;
  animation: scaleIn 0.2s var(--ease-spring);
}
.picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
}
.picker-title { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.picker-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration) var(--ease);
}
.picker-close:hover { background: var(--bg-subtle); color: var(--text); }
.picker-path {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-subtle);
}
.picker-up {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text-secondary);
  cursor: pointer;
  width: 24px;
  height: 24px;
  flex-shrink: 0;
}
.picker-path-text {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picker-list {
  flex: 1;
  min-height: 160px;
  overflow-y: auto;
  padding: 8px;
}
.picker-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: all var(--duration) var(--ease);
}
.picker-row:hover { background: var(--bg-subtle); }
.picker-row.active {
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border));
  color: var(--primary);
}
.picker-icon { width: 20px; text-align: center; flex-shrink: 0; }
.picker-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-go { color: var(--text-muted); }
.picker-empty, .picker-loading {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}
.picker-error { padding: 20px; color: var(--danger); font-size: 13px; }
.picker-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
}
.picker-selected {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.96); }
  to { opacity: 1; transform: scale(1); }
}
</style>
