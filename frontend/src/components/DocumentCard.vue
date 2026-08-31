<script setup lang="ts">
import type { Document } from '../types'

defineProps<{ doc: Document; deleting?: boolean }>()
const emit = defineEmits<{ delete: [id: string] }>()

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function fileExt(name: string): string {
  return (name.split('.').pop() || '').toUpperCase()
}
</script>

<template>
  <div class="document-card">
    <div class="doc-icon">
      <span>{{ fileExt(doc.filename) }}</span>
    </div>
    <div class="doc-info">
      <div class="doc-name">{{ doc.filename }}</div>
      <div class="doc-meta">
        <span>{{ formatSize(doc.size) }}</span>
        <span v-if="doc.chunk_count"> · {{ doc.chunk_count }} 个分块</span>
        <span> · {{ new Date(doc.created_at).toLocaleDateString() }}</span>
      </div>
    </div>
    <button class="btn btn-danger btn-sm" :disabled="deleting" @click="emit('delete', doc.id)">
      <span v-if="deleting" class="spinner-sm"></span>
      <span v-else>删除</span>
    </button>
  </div>
</template>

<style scoped>
.document-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  transition: all var(--duration) var(--ease);
  animation: fadeSlideUp 0.4s var(--ease);
}
.document-card:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}
.doc-icon {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: var(--primary);
  flex-shrink: 0;
  width: 46px;
  height: 46px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 12%, transparent), color-mix(in srgb, var(--accent) 8%, transparent));
  border: 1px solid color-mix(in srgb, var(--primary) 20%, transparent);
}
.doc-info { flex: 1; min-width: 0; }
.doc-name {
  font-weight: 700;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.doc-meta {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 3px;
}
.btn-sm {
  padding: 7px 14px;
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
