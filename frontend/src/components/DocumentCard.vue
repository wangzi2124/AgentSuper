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


<style scoped src="../styles/chat/documentCard.css"></style>
