<template>
  <div class="page-header">
    <h2>向量库</h2>
    <p>浏览和搜索存储在向量数据库中的分块</p>
  </div>
  <div class="page-content">
    <div class="toolbar">
      <div class="toolbar-search">
        <select v-model="selectedDocId" @change="onFilterChange" class="filter-select">
          <option value="">全部文档</option>
          <option v-for="doc in ds.documents" :key="doc.id" :value="doc.id">{{ doc.filename }} ({{ doc.chunk_count }} chunks)</option>
        </select>
        <div class="search-box">
          <input
            v-model="searchText"
            type="text"
            placeholder="搜索分块内容..."
            class="search-input"
            @keyup.enter="onSearch"
          />
          <button class="btn btn-primary btn-sm" @click="onSearch">搜索</button>
          <button v-if="vs.searchQuery" class="btn btn-sm" @click="searchText = ''; onSearch()">清除</button>
        </div>
      </div>
      <div class="toolbar-actions">
        <template v-if="vs.config">
          <span class="ttl-info">
            <template v-if="vs.config.ttl_days > 0">TTL {{ vs.config.ttl_days }} 天 · 每 {{ vs.config.cleanup_interval_hours }}h 检查</template>
            <template v-else>TTL 未启用</template>
          </span>
          <button v-if="vs.config.ttl_days > 0" class="btn btn-sm" @click="handleClearExpired">清理过期</button>
          <button
            v-if="(vs.config.pending_repair ?? 0) > 0"
            class="btn btn-sm btn-repair"
            @click="handleRepair"
          >自愈重建 ({{ vs.config!.pending_repair }})</button>
        </template>
        <button class="btn btn-sm btn-danger" @click="handleClearAll">清空向量库</button>
      </div>
    </div>

    <div class="result-meta">
      {{ vs.total }} 个分块
      <template v-if="vs.searchQuery"> · 匹配 "{{ vs.searchQuery }}"</template>
      <template v-if="vs.chunks.length > 0"> · 第 {{ currentPage }} / {{ totalPages }} 页</template>
    </div>

    <div v-if="vs.loading && vs.chunks.length === 0" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="vs.chunks.length === 0" class="empty-state">
      <div class="icon">📦</div>
      <p>未找到分块</p>
      <p style="font-size:13px;margin-top:4px;">请先上传文档以填充向量库。</p>
    </div>

    <div v-else>
      <div class="table-wrap">
        <table class="chunk-table">
          <thead>
            <tr>
              <th>#</th>
              <th>来源/定位</th>
              <th>章节/流程</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(chunk, i) in vs.chunks" :key="chunk.id">
              <td class="row-idx">{{ vs.offset + i + 1 }}</td>
              <td class="row-source">
                <code>{{ chunk.id.slice(0, 12) }}...</code>
                <span class="source-name">{{ sourceName(chunk.metadata) }}</span>
                <span v-if="offsetLabel(chunk.metadata)" class="meta-sub">偏移 {{ offsetLabel(chunk.metadata) }}</span>
                <span v-if="chunkTypeLabel(chunk)" class="meta-sub">{{ chunkTypeLabel(chunk) }}</span>
              </td>
              <td class="row-section">
                <div v-if="chunk.metadata.chapter_title || chunk.metadata.section_title" class="badge-wrap">
                  <span class="badge badge-enabled">{{ String(chunk.metadata.chapter_title || chunk.metadata.section_title) }}</span>
                </div>
                <template v-if="isProcessChunk(chunk.metadata)">
                  <div class="process-line"><strong>{{ String(chunk.metadata.process_name || '') }}</strong></div>
                  <div class="meta-sub">步骤 {{ String(chunk.metadata.step_no ?? '-') }}</div>
                  <div v-if="metaStr(chunk.metadata.role)" class="meta-sub">角色：{{ metaStr(chunk.metadata.role) }}</div>
                  <div v-if="metaStr(chunk.metadata.prev_step) || metaStr(chunk.metadata.next_step)" class="meta-sub">
                    前/后：{{ metaStr(chunk.metadata.prev_step) || '-' }} / {{ metaStr(chunk.metadata.next_step) || '-' }}
                  </div>
                </template>
                <template v-else>
                  <span v-if="!chunk.metadata.chapter_title && !chunk.metadata.section_title" class="badge badge-disabled">-</span>
                </template>
              </td>
              <td>
                <div v-if="processMetaSummary(chunk.metadata)" class="process-meta-box">{{ processMetaSummary(chunk.metadata) }}</div>
                <pre class="row-text">{{ chunk.text.slice(0, 420) }}{{ chunk.text.length > 420 ? '...' : '' }}</pre>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" class="pagination">
        <button class="btn btn-sm" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">上一页</button>
        <span class="page-info">
          <select :value="currentPage" @change="goToPage(Number(($event.target as HTMLSelectElement).value))" class="page-select">
            <option v-for="p in totalPages" :key="p" :value="p">第 {{ p }} 页</option>
          </select>
          / {{ totalPages }}
        </span>
        <button class="btn btn-sm" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">下一页</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useVectorStore } from '../stores/vectors'
import { useDocumentStore } from '../stores/documents'

const vs = useVectorStore()
const ds = useDocumentStore()

const selectedDocId = ref('')
const searchText = ref('')
const currentPage = ref(1)

const totalPages = computed(() => Math.ceil(vs.total / vs.limit))

onMounted(() => {
  ds.fetchAll()
  vs.fetch()
  vs.loadConfig()
})

function onFilterChange() {
  currentPage.value = 1
  vs.reset(selectedDocId.value, searchText.value)
}

function onSearch() {
  currentPage.value = 1
  vs.offset = 0
  vs.reset(selectedDocId.value, searchText.value)
}

function goToPage(page: number) {
  if (page < 1 || page > totalPages.value) return
  currentPage.value = page
  vs.offset = (page - 1) * vs.limit
  vs.fetch()
}

async function handleClearAll() {
  const confirmMsg = '确定要清空全部知识库数据吗？\n\n将删除：\n- 向量库全部分块\n- 章节库全部记录\n- BM25 索引\n- 全部上传文件\n\n此操作不可撤销。'
  if (!confirm(confirmMsg)) return
  try {
    await vs.clearAll()
    ds.fetchAll()
    alert('知识库数据已全部清空')
  } catch (e: any) {
    alert(`清空失败：${e.message || e}`)
  }
}

async function handleClearExpired() {
  if (!confirm('按 TTL 配置清理所有过期文档？')) return
  try {
    const res = await vs.clearExpired()
    ds.fetchAll()
    alert(res.message)
  } catch (e: any) {
    alert(`清理失败：${e.message || e}`)
  }
}

async function handleRepair() {
  const pending = vs.config?.pending_repair ?? 0
  if (!confirm(`检测到 ${pending} 个文档的索引未就绪，将重新构建其向量/BM25/章节数据？\n（幂等操作，可重复执行）`)) return
  try {
    const res = await vs.repair()
    ds.fetchAll()
    const failed = res.failed?.length ?? 0
    alert(failed > 0
      ? `${res.message}\n${failed} 个索引重建失败：${res.failed!.map((f) => f.error).join('；')}`
      : res.message)
  } catch (e: any) {
    alert(`修复失败：${e.message || e}`)
  }
}

function sourceName(meta: Record<string, unknown>) {
  return String(meta.filename || meta.source || meta.source_file || '-')
}
function metaStr(v: unknown) {
  return v == null ? '' : String(v).trim()
}
function isProcessChunk(meta: Record<string, unknown>) {
  return !!(meta.is_process_step || meta.process_name)
}
function offsetLabel(meta: Record<string, unknown>) {
  const s = meta.chunk_start
  const e = meta.chunk_end
  if (s == null || e == null || s === '' || e === '') return ''
  return `${s}-${e}`
}
function chunkTypeLabel(chunk: { text: string; metadata: Record<string, unknown> }) {
  const m = chunk.metadata
  if (m.is_process_step) return '流程步骤'
  if (m.is_faq) return 'FAQ'
  if (m.is_table) return '表格'
  if (m.is_parent) return '父块'
  if (m.is_dialogue) return '对话'
  return ''
}
function processMetaSummary(meta: Record<string, unknown>) {
  if (!isProcessChunk(meta)) return ''
  const parts: string[] = []
  if (metaStr(meta.precondition)) parts.push(`前置:${metaStr(meta.precondition)}`)
  if (metaStr(meta.input)) parts.push(`输入:${metaStr(meta.input)}`)
  if (metaStr(meta.output)) parts.push(`输出:${metaStr(meta.output)}`)
  if (metaStr(meta.branch)) parts.push(`分支:${metaStr(meta.branch)}`)
  if (metaStr(meta.exception)) parts.push(`异常:${metaStr(meta.exception)}`)
  return parts.join(' | ')
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.toolbar-search { display: flex; gap: 12px; flex-wrap: wrap; flex: 1; min-width: 260px; }
.toolbar-actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.ttl-info { font-size: 12px; color: var(--text-secondary); }
.btn-sm { padding: 7px 14px; font-size: 12px; }
.btn-repair { border-color: var(--warning); color: var(--warning); background: var(--warning-soft); }
.btn-repair:hover { background: color-mix(in srgb, var(--warning) 12%, var(--surface)); color: var(--warning); }

.filter-select {
  flex: 1;
  max-width: 240px;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.filter-select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.search-box { display: flex; gap: 6px; flex: 1 1 280px; min-width: 220px; }
.search-input {
  flex: 1;
  padding: 9px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.search-input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }

.result-meta { font-size: 13px; color: var(--text-secondary); margin-bottom: 14px; font-weight: 500; }
.loading-wrap { display: flex; justify-content: center; padding: 40px; }

.table-wrap {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: auto;
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}
.chunk-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.chunk-table th {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
  white-space: nowrap;
  text-align: left;
  font-size: 12px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: var(--bg-subtle);
}
.chunk-table td { padding: 12px 14px; border-bottom: 1px solid var(--border-subtle); vertical-align: top; }
.chunk-table tr:last-child td { border-bottom: none; }
.chunk-table tr { transition: background 0.15s; }
.chunk-table tbody tr:hover { background: var(--bg-subtle); }
.row-idx { color: var(--text-muted); font-size: 11px; white-space: nowrap; }
.row-source { white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
.row-source code { font-size: 10px; display: block; color: var(--text-muted); }
.source-name { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta-sub { display: block; font-size: 11px; color: var(--text-muted); margin-top: 2px; white-space: normal; }
.row-section { min-width: 180px; max-width: 260px; }
.badge-wrap { margin-bottom: 4px; }
.process-line { font-size: 12px; color: var(--text); margin-bottom: 2px; }
.process-meta-box {
  margin-bottom: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
}
.row-text {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  max-height: 160px;
  overflow-y: auto;
  font-family: inherit;
  color: var(--text);
}

.pagination { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 16px; }
.page-info { font-size: 13px; color: var(--text-secondary); }
.page-select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
}
</style>
