<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useGeneratedStore } from '../stores/generated'

const store = useGeneratedStore()
const deleting = ref<Set<string>>(new Set())

onMounted(() => {
  store.fetchAll()
})

async function handleDelete(filename: string) {
  if (!confirm(`Delete "${filename}"?`)) return
  deleting.value.add(filename)
  try {
    await store.remove(filename)
  } catch (err: any) {
    alert(err.message)
  } finally {
    deleting.value.delete(filename)
  }
}

function getFileUrl(filename: string): string {
  return `/api/generated/download/${encodeURIComponent(filename)}`
}
</script>

<template>
  <div class="page-header">
    <h2>Generated Files</h2>
    <p>Word documents created by the AI agent</p>
  </div>
  <div class="page-content">
    <div class="search-bar">
      <input
        v-model="store.searchQuery"
        type="text"
        placeholder="Search by filename..."
        class="search-input"
      />
    </div>

    <div v-if="store.loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="store.filteredFiles.length === 0" class="empty-state">
      <div class="icon">📝</div>
      <p>{{ store.searchQuery ? 'No matching files' : 'No generated files yet' }}</p>
      <p style="font-size:13px;margin-top:4px;">
        {{ store.searchQuery ? 'Try a different search term.' : 'Ask the AI agent to create a Word document.' }}
      </p>
    </div>

    <div v-else class="file-list">
      <div class="file-count">{{ store.filteredFiles.length }} file(s)</div>
      <div class="file-row" v-for="f in store.filteredFiles" :key="f.filename">
        <div class="file-info">
          <div class="file-icon">📄</div>
          <div class="file-details">
            <div class="file-name">{{ f.filename }}</div>
            <div class="file-meta">{{ store.formatSize(f.size) }}</div>
          </div>
        </div>
        <div class="file-actions">
          <a :href="getFileUrl(f.filename)" class="btn" download>Download</a>
          <button
            class="btn btn-danger"
            :disabled="deleting.has(f.filename)"
            @click="handleDelete(f.filename)"
          >
            {{ deleting.has(f.filename) ? 'Deleting...' : 'Delete' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-bar {
  margin-bottom: 16px;
}

.search-input {
  width: 100%;
  max-width: 400px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 14px;
  background: var(--surface);
  color: var(--text);
  outline: none;
}

.search-input:focus {
  border-color: var(--primary);
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-count {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.file-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.file-info {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.file-icon {
  font-size: 24px;
  flex-shrink: 0;
}

.file-details {
  min-width: 0;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 360px;
}

.file-meta {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.file-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
