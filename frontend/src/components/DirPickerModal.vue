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


<style scoped src="../styles/chat/dirPickerModal.css"></style>
