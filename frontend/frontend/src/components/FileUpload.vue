<script setup lang="ts">
import { ref, computed } from 'vue'

const emit = defineEmits<{ upload: [file: File] }>()
const props = defineProps<{ loading: boolean; progress?: number; stage?: string }>()

const dragOver = ref(false)
const inputRef = ref<HTMLInputElement>()

const uploading = computed(() => !!(props.stage) || (props.loading && (props.progress ?? 0) > 0))

function onFileInput(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files && files[0]) {
    emit('upload', files[0])
  }
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (files && files[0]) {
    emit('upload', files[0])
  }
}

function triggerInput() {
  inputRef.value?.click()
}
</script>

<template>
  <div
    class="file-upload"
    :class="{ 'drag-over': dragOver, uploading }"
    @dragover.prevent="dragOver = true"
    @dragleave="dragOver = false"
    @drop.prevent="onDrop"
    @click="uploading ? undefined : triggerInput()"
  >
    <input
      ref="inputRef"
      type="file"
      accept=".txt,.md,.pdf"
      style="display: none"
      @change="onFileInput"
    />
    <div v-if="!uploading" class="upload-icon">📁</div>
    <div v-else class="spinner"></div>
    <p v-if="!uploading">Drop files here or click to upload</p>
    <p v-else class="upload-status">{{ stage || 'Uploading...' }}</p>
    <p class="hint">Supports .txt, .md, .pdf</p>
    <div v-if="uploading" class="progress-bar">
      <div class="progress-fill" :style="{ width: (progress ?? 0) + '%' }"></div>
    </div>
    <p v-if="uploading" class="progress-text">{{ progress ?? 0 }}%</p>
  </div>
</template>

<style scoped>
.file-upload {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 32px;
  text-align: center;
  cursor: pointer;
  transition: all 0.15s;
  color: var(--text-secondary);
}

.file-upload:hover,
.file-upload.drag-over {
  border-color: var(--primary);
  background: #eef2ff;
  color: var(--primary);
}

.file-upload.uploading {
  pointer-events: none;
  cursor: default;
}

.upload-icon {
  font-size: 36px;
  margin-bottom: 8px;
}

.hint {
  font-size: 12px;
  margin-top: 4px;
}

.upload-status {
  font-weight: 500;
}

.progress-bar {
  margin-top: 12px;
  height: 8px;
  background: var(--border);
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary), #6366f1);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 13px;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
</style>
