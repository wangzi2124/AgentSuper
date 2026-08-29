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
  { name: '全部文档', subname: `${ds.documents.reduce((n, d) => n + d.chunk_count, 0)} chunks`, value: '' },
  ...ds.documents.map(d => ({ name: d.filename, subname: `${d.chunk_count} chunks`, value: d.id })),
])

function selectedDocLabel() {
  const hit = docOptions.value.find(o => o.value === selectedDocId.value)
  return hit ? hit.name : '全部文档'
}

const chunksTitle = computed(() =>
  `${vs.total} 个分块${vs.searchQuery ? ` · 匹配 "${vs.searchQuery}"` : ''}`
)

function chunkSource(chunk: { metadata: Record<string, unknown> }): string {
  const f = chunk.metadata.filename
  const s = chunk.metadata.source
  return String(f || s || '-')
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
      message: '将删除向量库全部分块、章节库记录、BM25 索引和全部上传文件，此操作不可撤销！',
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
</script>

<template>
  <div class="m-vectors">
    <van-search
      v-model="searchText"
      placeholder="搜索分块内容..."
      show-action
      @search="onSearch"
      @cancel="onSearch"
    />

    <van-cell :title="chunksTitle" is-link @click="filterSheet = true">
      <template #label>{{ selectedDocLabel() }}</template>
    </van-cell>

    <van-loading v-if="vs.loading && vs.chunks.length === 0" class="loading" />
    <van-empty v-else-if="vs.chunks.length === 0" image="search" description="未找到分块" />

    <van-cell-group v-else inset class="chunk-list">
      <van-cell v-for="(chunk, i) in vs.chunks" :key="chunk.id" :title="chunkSource(chunk)">
        <template #label>
          <div v-if="chunk.metadata.chapter_title" class="chapter">📖 {{ chunk.metadata.chapter_title }}</div>
          <div class="chunk-text">{{ chunk.text.slice(0, 200) }}{{ chunk.text.length > 200 ? '...' : '' }}</div>
        </template>
        <template #right-icon>
          <div class="idx">{{ vs.offset + i + 1 }}</div>
        </template>
      </van-cell>
    </van-cell-group>

    <div v-if="hasMore" class="load-more">
      <van-button size="small" round plain type="primary" @click="loadMore">加载更多</van-button>
    </div>

    <div class="actions">
      <van-button size="small" v-if="vs.config && vs.config.ttl_days > 0" @click="handleClearExpired">
        清理过期（TTL {{ vs.config.ttl_days }} 天）
      </van-button>
      <van-button size="small" type="danger" plain @click="handleClearAll">清空向量库</van-button>
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
.m-vectors { padding-bottom: 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.chapter { color: #8b5cf6; font-size: 12px; margin-bottom: 4px; }
.chunk-text { color: #64748b; font-size: 13px; line-height: 1.6; word-break: break-word; }
.idx { font-size: 11px; color: #94a3b8; align-self: flex-start; }
.load-more { display: flex; justify-content: center; padding: 16px 0; }
.actions { display: flex; justify-content: flex-end; gap: 12px; padding: 12px 16px; }
</style>
