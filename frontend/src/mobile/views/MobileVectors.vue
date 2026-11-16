<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useVectorStore } from '../../stores/vectors'
import { useDocumentStore } from '../../stores/documents'
import { showConfirmDialog, showToast, showDialog } from 'vant'

const vs = useVectorStore()
const ds = useDocumentStore()

const searchText = ref('')
const filterSheet = ref(false)
const selectedDocId = ref('')

const totalPages = computed(() => Math.ceil(vs.total / vs.limit))
const hasMore = computed(() => vs.chunks.length < vs.total)

const docOptions = computed(() => [
  { name: '全部文档', value: '' },
  ...ds.documents.map(d => ({ name: d.filename, value: d.id })),
])

function selectedDocLabel() {
  const hit = docOptions.value.find(o => o.value === selectedDocId.value)
  return hit ? hit.name : '全部文档'
}

function chunkSource(chunk: { metadata: Record<string, unknown> }): string {
  const m = chunk.metadata
  return String(m.filename || m.source || m.source_file || '-')
}
function metaStr(v: unknown) {
  return v == null ? '' : String(v).trim()
}
function isProcessChunk(meta: Record<string, unknown>) {
  return !!(meta.is_process_step || meta.process_name)
}
function offsetLabel(meta: Record<string, unknown>) {
  const s = meta.chunk_start, e = meta.chunk_end
  if (s == null || e == null || s === '' || e === '') return ''
  return `${s}-${e}`
}
function chunkTypeLabel(meta: Record<string, unknown>) {
  if (meta.is_process_step) return '流程步骤'
  if (meta.is_faq) return 'FAQ'
  if (meta.is_table) return '表格'
  if (meta.is_parent) return '父块'
  if (meta.is_dialogue) return '对话'
  return ''
}
function processMetaSummary(meta: Record<string, unknown>) {
  if (!isProcessChunk(meta)) return ''
  const parts: string[] = []
  if (metaStr(meta.role)) parts.push(`角色:${metaStr(meta.role)}`)
  if (metaStr(meta.input)) parts.push(`输入:${metaStr(meta.input)}`)
  if (metaStr(meta.output)) parts.push(`输出:${metaStr(meta.output)}`)
  if (metaStr(meta.branch)) parts.push(`分支:${metaStr(meta.branch)}`)
  if (metaStr(meta.exception)) parts.push(`异常:${metaStr(meta.exception)}`)
  return parts.join(' | ')
}

onMounted(() => {
  ds.fetchAll()
  vs.fetch()
  vs.loadConfig()
})

function onSearch() {
  vs.reset(selectedDocId.value, searchText.value.trim())
}

function onPickDoc(option: any) {
  const val = (option && option.value) as string | undefined
  selectedDocId.value = val || ''
  vs.reset(val || '', searchText.value.trim())
}

function loadMore() {
  vs.offset += vs.limit
  vs.fetch(true)
}

async function handleClearAll() {
  try {
    await showConfirmDialog({
      title: '清空向量库',
      message: '将删除向量库全部分块、章节库记录、BM25 索引和全部上传文件，此操作不可撤销。',
    })
    await vs.clearAll()
    ds.fetchAll()
    showToast('已清空')
  } catch (err: any) {
    if (err?.message && !String(err.message).includes('cancel')) showToast(err.message || '清空失败')
  }
}

async function handleClearExpired() {
  try {
    await showConfirmDialog({ title: '清理过期', message: '按 TTL 配置清理所有过期文档？' })
    const res = await vs.clearExpired()
    ds.fetchAll()
    showDialog({ title: '清理过期', message: res.message })
  } catch (err: any) {
    if (err?.message && !String(err.message).includes('cancel')) showToast(err.message || '清理失败')
  }
}

async function handleRepair() {
  const pending = vs.config?.pending_repair ?? 0
  try {
    await showConfirmDialog({
      title: '自愈重建',
      message: `${pending} 个文档索引未就绪，重新构建其向量/BM25/章节数据？`,
    })
    const res = await vs.repair()
    ds.fetchAll()
    showDialog({
      title: '自愈重建',
      message: res.message + (res.failed?.length ? `\n${res.failed.length} 个失败` : ''),
    })
  } catch (err: any) {
    if (err?.message && !String(err.message).includes('cancel')) showToast(err.message || '修复失败')
  }
}
</script>

<template>
  <div class="m-vectors">
    <div class="m-query-card">
      <div class="m-query-line">
        <van-search
          v-model="searchText"
          placeholder="搜索分块内容..."
          shape="round"
          @search="onSearch"
          @clear="onSearch"
        />
        <button class="m-query-filter" @click="filterSheet = true">
          <van-icon name="filter-o" />
          <span class="m-query-filter-label">{{ selectedDocLabel() }}</span>
          <van-icon name="arrow-down" />
        </button>
      </div>
    </div>

    <div class="m-hero">
      <div class="m-hero-icon"><van-icon name="apps-o" /></div>
      <div class="m-hero-meta">
        <div class="m-hero-label">向量库</div>
        <div class="m-hero-value">{{ vs.total }} 个分块</div>
      </div>
      <div class="m-hero-extra">
        <div class="m-hero-mini"><span class="m-hero-num">{{ ds.documents.length }}</span> 文档</div>
        <div class="m-hero-mini" v-if="vs.searchQuery"><span class="m-hero-num">{{ vs.chunks.length }}</span> 匹配</div>
      </div>
      <button class="m-hero-clear" @click="handleClearAll" aria-label="清空向量库">
        <van-icon name="delete-o" /><span>清空</span>
      </button>
    </div>

    <van-loading v-if="vs.loading && vs.chunks.length === 0" class="loading" />
    <van-empty v-else-if="vs.chunks.length === 0" image="search" description="未找到分块" />

    <div v-else class="chunk-list">
      <div v-for="(chunk, i) in vs.chunks" :key="chunk.id" class="m-chunk-card">
        <div class="m-chunk-head">
          <div v-if="chunk.metadata.chapter_title || chunk.metadata.section_title" class="m-chunk-chapter">
            <van-icon name="orders-o" /> {{ String(chunk.metadata.chapter_title || chunk.metadata.section_title) }}
          </div>
          <div class="m-chunk-idx">#{{ vs.offset + i + 1 }}</div>
        </div>
        <div class="m-chunk-src">{{ chunkSource(chunk) }}</div>
        <div class="m-chunk-tags">
          <span v-if="chunkTypeLabel(chunk.metadata)" class="m-tag">{{ chunkTypeLabel(chunk.metadata) }}</span>
          <span v-if="offsetLabel(chunk.metadata)" class="m-tag subtle">偏移 {{ offsetLabel(chunk.metadata) }}</span>
          <span v-if="isProcessChunk(chunk.metadata)" class="m-tag process">步骤 {{ String(chunk.metadata.step_no ?? '-') }}</span>
        </div>
        <div v-if="isProcessChunk(chunk.metadata)" class="m-chunk-process">
          <div class="m-process-name">{{ String(chunk.metadata.process_name || '') }}</div>
          <div class="m-process-meta">
            <span v-if="metaStr(chunk.metadata.prev_step) || metaStr(chunk.metadata.next_step)">前/后: {{ metaStr(chunk.metadata.prev_step) || '-' }} / {{ metaStr(chunk.metadata.next_step) || '-' }}</span>
          </div>
          <div v-if="processMetaSummary(chunk.metadata)" class="m-process-meta">{{ processMetaSummary(chunk.metadata) }}</div>
        </div>
        <div class="m-chunk-text">{{ chunk.text.slice(0, 260) }}{{ chunk.text.length > 260 ? '...' : '' }}</div>
      </div>
    </div>

    <div v-if="hasMore" class="load-more">
      <van-button size="small" round plain type="primary" @click="loadMore">加载更多</van-button>
    </div>

    <div v-if="vs.config && ((vs.config.ttl_days ?? 0) > 0 || (vs.config.pending_repair ?? 0) > 0)" class="m-manage-bar">
      <div class="m-manage-left">
        <button
          v-if="vs.config.ttl_days > 0"
          class="m-manage-btn"
          @click="handleClearExpired"
        >
          <van-icon name="clock-o" /><span>清理过期</span>
        </button>
        <button
          v-if="(vs.config.pending_repair ?? 0) > 0"
          class="m-manage-btn repair"
          @click="handleRepair"
        >
          <van-icon name="rebuild" /><span>自愈重建 ({{ vs.config!.pending_repair }})</span>
        </button>
      </div>
    </div>

    <van-action-sheet
      v-model:show="filterSheet"
      :actions="docOptions"
      title="按文档筛选"
      cancel-text="取消"
      @select="onPickDoc"
    />
  </div>
</template>

<style scoped>
.m-vectors { padding: 8px 12px 24px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }

.m-query-card { margin-bottom: 10px; }
.m-query-line { display: flex; align-items: center; gap: 8px; }
.m-query-filter { border: none; background: var(--bg-subtle, #f5f6f8); border-radius: 999px; padding: 8px 10px; font-size: 12px; display: inline-flex; align-items: center; gap: 4px; max-width: 42%; }
.m-query-filter-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.m-hero {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 4px 0 12px;
  padding: 14px 16px;
  border-radius: var(--m-card-radius, 16px);
  background: linear-gradient(135deg, #059669, #10b981 55%, #34d399);
  box-shadow: 0 6px 18px rgba(16, 185, 129, 0.28);
  color: #fff;
}
.m-hero-icon { width: 40px; height: 40px; border-radius: 13px; background: rgba(255,255,255,.2); display:flex; align-items:center; justify-content:center; font-size:20px; flex-shrink:0; }
.m-hero-meta { flex: 1; min-width: 0; }
.m-hero-label { font-size: 12px; opacity: .85; }
.m-hero-value { font-size: 20px; font-weight: 700; margin-top: 2px; }
.m-hero-extra { text-align: right; flex-shrink: 0; }
.m-hero-mini { font-size: 11px; opacity: .92; margin-top: 2px; }
.m-hero-num { font-weight: 700; font-size: 13px; }
.m-hero-clear { display: inline-flex; align-items: center; gap: 4px; border: 1px solid rgba(255,255,255,.35); background: rgba(255,255,255,.14); color: #fff; border-radius: 999px; padding: 6px 10px; font-size: 12px; }

.chunk-list { display: flex; flex-direction: column; gap: 10px; }
.m-chunk-card { background: var(--surface, #fff); border: 1px solid var(--border, #e8e8e8); border-radius: 14px; padding: 12px; }
.m-chunk-head { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; margin-bottom: 4px; }
.m-chunk-chapter { font-size: 12px; font-weight: 600; color: var(--text, #222); display:flex; align-items:center; gap:4px; }
.m-chunk-idx { font-size: 11px; color: var(--text-muted, #999); }
.m-chunk-src { font-size: 12px; color: var(--text-secondary, #666); margin-bottom: 6px; }
.m-chunk-tags { display:flex; flex-wrap:wrap; gap:6px; margin-bottom: 6px; }
.m-tag { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #ecfdf5; color: #047857; }
.m-tag.subtle { background: #f3f4f6; color: #4b5563; }
.m-tag.process { background: #eff6ff; color: #1d4ed8; }
.m-chunk-process { margin-bottom: 6px; padding: 8px; border-radius: 10px; background: #f8fafc; }
.m-process-name { font-size: 12px; font-weight: 600; margin-bottom: 2px; }
.m-process-meta { font-size: 11px; color: #475569; line-height: 1.4; margin-top: 2px; }
.m-chunk-text { font-size: 12px; line-height: 1.55; color: var(--text, #222); white-space: pre-wrap; word-break: break-word; }

.load-more { display:flex; justify-content:center; padding: 12px 0 4px; }
.m-manage-bar { margin-top: 12px; padding: 10px 4px 0; }
.m-manage-left { display:flex; gap: 10px; flex-wrap: wrap; }
.m-manage-btn { border: 1px solid var(--border, #e5e7eb); background: var(--surface, #fff); border-radius: 999px; padding: 8px 12px; font-size: 12px; display:inline-flex; align-items:center; gap:6px; }
.m-manage-btn.repair { color: #b45309; border-color: #fcd34d; background: #fffbeb; }
</style>
