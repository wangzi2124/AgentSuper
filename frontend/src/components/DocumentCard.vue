<script setup lang="ts">
import type { Document } from '../types'

// 定义组件属性：文档对象和删除状态
defineProps<{ doc: Document; deleting?: boolean }>()
// 定义组件事件：删除文档
const emit = defineEmits<{ delete: [id: string] }>()

// 格式化文件大小显示
function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
</script>

<template>
  <div class="card document-card">
    <div class="doc-icon">📄</div>
    <div class="doc-info">
      <div class="doc-name">{{ doc.filename }}</div>
      <div class="doc-meta">
        <span>{{ formatSize(doc.size) }}</span>
        <span v-if="doc.chunk_count"> · {{ doc.chunk_count }} chunks</span>
        <span> · {{ new Date(doc.created_at).toLocaleDateString() }}</span>
      </div>
    </div>
    <button class="btn btn-danger btn-sm" :disabled="deleting" @click="emit('delete', doc.id)">
      <span v-if="deleting" class="spinner-sm"></span>
      <span v-else>Delete</span>
    </button>
  </div>
</template>

<style scoped>
.document-card {
  display: flex;
  align-items: center;
  gap: 14px;
}

.doc-icon {
  font-size: 28px;
  flex-shrink: 0;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.btn-sm {
  padding: 6px 12px;
  font-size: 12px;
  min-width: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.spinner-sm {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
