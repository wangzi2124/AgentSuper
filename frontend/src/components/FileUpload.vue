<script setup lang="ts">
import { ref, computed } from 'vue'

// 定义组件事件：上传文件
const emit = defineEmits<{ upload: [file: File] }>()
// 定义组件属性：加载状态、进度、阶段
const props = defineProps<{ loading: boolean; progress?: number; stage?: string }>()

// 拖拽悬停状态
const dragOver = ref(false)
// 文件输入元素引用
const inputRef = ref<HTMLInputElement>()

// 是否正在上传中
const uploading = computed(() => !!(props.stage) || (props.loading && (props.progress ?? 0) > 0))

// 文件选择事件处理
function onFileInput(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files && files[0]) {
    emit('upload', files[0])
  }
}

// 拖拽释放事件处理
function onDrop(e: DragEvent) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (files && files[0]) {
    emit('upload', files[0])
  }
}

// 触发文件选择对话框
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
    <p v-if="!uploading">将文件拖到此处，或点击上传</p>
    <p v-else class="upload-status">{{ stage || '上传中...' }}</p>
    <p class="hint">支持 .txt、.md、.pdf</p>
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
