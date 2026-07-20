<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useVectorStore } from '../stores/vectors'
import { useDocumentStore } from '../stores/documents'

// 向量存储状态管理
const vs = useVectorStore()
// 文档状态管理
const ds = useDocumentStore()

// 选中的文档ID（用于筛选）
const selectedDocId = ref('')
// 搜索关键词
const searchText = ref('')
// 当前页码
const currentPage = ref(1)

// 计算属性：总页数
const totalPages = computed(() => Math.ceil(vs.total / vs.limit))

// 组件挂载时加载文档和向量数据
onMounted(() => {
  ds.fetchAll()
  vs.fetch()
})

// 筛选条件变化时重置分页并刷新
function onFilterChange() {
  currentPage.value = 1
  vs.reset(selectedDocId.value, searchText.value)
}

// 执行搜索
function onSearch() {
  currentPage.value = 1
  vs.offset = 0
  vs.reset(selectedDocId.value, searchText.value)
}

// 跳转到指定页码
function goToPage(page: number) {
  if (page < 1 || page > totalPages.value) return
  currentPage.value = page
  vs.offset = (page - 1) * vs.limit
  vs.fetch()
}
</script>

<template>
  <div class="page-header">
    <h2>Vector Store</h2>
    <p>Browse and search chunks stored in the vector database</p>
  </div>
  <div class="page-content">
    <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
      <select v-model="selectedDocId" @change="onFilterChange" style="flex:1;min-width:180px;padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;">
        <option value="">All documents</option>
        <option v-for="doc in ds.documents" :key="doc.id" :value="doc.id">{{ doc.filename }} ({{ doc.chunk_count }} chunks)</option>
      </select>
      <div style="display:flex;gap:4px;flex:2;min-width:200px;">
        <input
          v-model="searchText"
          type="text"
          placeholder="Search chunk content..."
          style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;"
          @keyup.enter="onSearch"
        />
        <button class="btn" @click="onSearch">Search</button>
        <button v-if="vs.searchQuery" class="btn" @click="searchText = ''; onSearch()">Clear</button>
      </div>
    </div>

    <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">
      {{ vs.total }} chunk(s)
      <template v-if="vs.searchQuery"> — matching "{{ vs.searchQuery }}"</template>
      <template v-if="vs.chunks.length > 0"> — page {{ currentPage }} / {{ totalPages }}</template>
    </div>

    <div v-if="vs.loading && vs.chunks.length === 0" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="vs.chunks.length === 0" class="empty-state">
      <div class="icon">📦</div>
      <p>No chunks found</p>
      <p style="font-size:13px;margin-top:4px;">Upload documents first to populate the vector store.</p>
    </div>

    <div v-else>
      <div style="border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead>
            <tr style="background:var(--bg);text-align:left;">
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;white-space:nowrap;">#</th>
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;">Source</th>
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;">Chapter</th>
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;">Content</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(chunk, i) in vs.chunks" :key="chunk.id" style="border-bottom:1px solid var(--border);">
              <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;color:var(--text-secondary);font-size:11px;">
                {{ vs.offset + i + 1 }}
              </td>
              <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;max-width:160px;overflow:hidden;text-overflow:ellipsis;font-size:12px;">
                <code style="font-size:10px;display:block;color:var(--text-secondary);">{{ chunk.id.slice(0, 12) }}...</code>
                <span>{{ chunk.metadata.filename || chunk.metadata.source || '-' }}</span>
              </td>
              <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;font-size:12px;">
                <span v-if="chunk.metadata.chapter_title" class="badge badge-enabled">{{ chunk.metadata.chapter_title }}</span>
                <span v-else class="badge badge-disabled">-</span>
              </td>
              <td style="padding:8px 10px;vertical-align:top;">
                <pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;line-height:1.5;max-height:120px;overflow-y:auto;font-family:inherit;">{{ chunk.text.slice(0, 300) }}{{ chunk.text.length > 300 ? '...' : '' }}</pre>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" style="display:flex;align-items:center;justify-content:center;gap:8px;margin-top:12px;">
        <button class="btn" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">Prev</button>
        <span style="font-size:13px;color:var(--text-secondary);">
          <select :value="currentPage" @change="goToPage(Number(($event.target as HTMLSelectElement).value))" style="padding:4px 6px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;">
            <option v-for="p in totalPages" :key="p" :value="p">Page {{ p }}</option>
          </select>
          / {{ totalPages }}
        </span>
        <button class="btn" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">Next</button>
      </div>
    </div>
  </div>
</template>
