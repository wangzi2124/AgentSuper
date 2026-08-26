<script setup lang="ts">
import { ref, watch } from 'vue'
import { browseDirectories } from '../api/permission'

const props = defineProps<{ show: boolean }>()

const emit = defineEmits<{ close: []; select: [path: string] }>()

const currentPath = ref('')
const currentName = ref('')
const parentPath = ref('')
const dirs = ref<Array<{ name: string; path: string }>>([])
const loading = ref(false)
const error = ref('')

async function load(path: string) {
  loading.value = true
  error.value = ''
  try {
    const data = await browseDirectories(path)
    currentPath.value = data.path
    currentName.value = data.name
    parentPath.value = data.parent
    dirs.value = data.dirs
  } catch (e: any) {
    error.value = e?.message || '加载目录失败'
    dirs.value = []
  } finally {
    loading.value = false
  }
}

function openDir(dir: { path: string }) {
  load(dir.path)
}

function goUp() {
  if (parentPath.value) load(parentPath.value)
  else load('')
}

watch(() => props.show, (v) => {
  if (v) {
    currentPath.value = ''
    currentName.value = ''
    parentPath.value = ''
    dirs.value = []
    error.value = ''
    load('')
  }
})

function confirm() {
  if (currentPath.value) emit('select', currentPath.value)
}

function cancel() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="dir-modal-overlay" @click.self="cancel">
      <div class="dir-modal">
      <div class="dir-modal-header">
        <span class="dir-modal-title">选择目录</span>
        <button class="dir-modal-close" @click="cancel" title="关闭">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>

      <div class="dir-path">
        <button v-if="currentPath" class="dir-up" @click="goUp" title="返回上级/盘符列表">↑</button>
        <span class="dir-path-text" :title="currentPath">{{ currentPath || '请选择盘符/目录' }}</span>
      </div>

      <p v-if="error" class="dir-error">{{ error }}</p>
      <p v-else-if="loading" class="dir-loading">加载中...</p>

      <div v-else class="dir-list">
        <div v-if="dirs.length === 0" class="dir-empty">该目录下没有子目录</div>
        <div v-for="d in dirs" :key="d.path" class="dir-item" :title="d.path" @dblclick="openDir(d)">
          <button class="dir-item-main" @click="openDir(d)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            <span class="dir-item-name">{{ d.name }}</span>
          </button>
          <button class="dir-item-open" @click="openDir(d)" title="进入">›</button>
        </div>
      </div>

      <div class="dir-modal-footer">
        <button class="dir-btn dir-btn-secondary" @click="cancel">取消</button>
        <button class="dir-btn dir-btn-primary" :disabled="!currentPath" @click="confirm">选择此目录</button>
      </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dir-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.dir-modal {
  width: 460px;
  max-width: 90vw;
  max-height: 80vh;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.dir-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.dir-modal-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.dir-modal-close {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dir-modal-close:hover { background: var(--bg); color: var(--text); }
.dir-path {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}
.dir-up {
  width: 24px;
  height: 24px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  flex-shrink: 0;
  line-height: 1;
}
.dir-up:hover { border-color: var(--primary); color: var(--primary); }
.dir-path-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  color: var(--text);
}
.dir-error { padding: 12px 16px; font-size: 12px; color: #ef4444; }
.dir-loading { padding: 16px; text-align: center; font-size: 13px; color: var(--text-secondary); }
.dir-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
  min-height: 160px;
  max-height: 45vh;
}
.dir-empty { padding: 24px 0; text-align: center; font-size: 13px; color: var(--text-secondary); }
.dir-item {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: 6px;
}
.dir-item:hover { background: var(--bg); }
.dir-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: none;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}
.dir-item-main svg { color: #f59e0b; flex-shrink: 0; }
.dir-item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dir-item-open {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 16px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dir-item-open:hover { color: var(--primary); }
.dir-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}
.dir-btn {
  padding: 6px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
}
.dir-btn-secondary:hover { border-color: var(--text-secondary); }
.dir-btn-primary {
  background: var(--primary, #4f46e5);
  border-color: var(--primary, #4f46e5);
  color: #fff;
}
.dir-btn-primary:hover { opacity: 0.9; }
.dir-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
