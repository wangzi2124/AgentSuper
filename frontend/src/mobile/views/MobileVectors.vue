<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useVectorStore } from '../../stores/vectors'
import { useDocumentStore } from '../../stores/documents'
import { showConfirmDialog, showToast, showDialog } from 'vant'

const vs = useVectorStore()
const ds = useDocumentStore()

const searchText = ref('')
const selectedDocId = ref('')

const totalPages = computed(() => Math.ceil(vs.total / vs.limit))
const hasMore = computed(() => vs.chunks.length < vs.total)

const docOptions = computed(() => [
  { name: '全部文档', value: '' },
  ...ds.documents.map(d => ({ name: d.filename, value: d.id })),
])

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
    <!-- 查询卡：搜索 + 文档筛选 合一（去除多余的搜索/取消按钮） -->
    <div class="m-query-card">
      <van-search
        v-model="searchText"
        placeholder="搜索分块内容..."
        shape="round"
        @search="onSearch"
        @clear="onSearch"
      />
      <div class="m-query-row">
        <span
          v-for="opt in docOptions"
          :key="opt.value || '__all__'"
          class="m-filter-chip"
          :class="{ active: selectedDocId === opt.value }"
          @click="onPickDoc(opt)"
        >{{ opt.name }}</span>
      </div>
    </div>

    <!-- 统计横幅（绿色系）+ 清空向量库 -->
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
          <div v-if="chunk.metadata.chapter_title" class="m-chunk-chapter">
            <van-icon name="orders-o" /> {{ chunk.metadata.chapter_title }}
          </div>
          <div class="m-chunk-idx">#{{ vs.offset + i + 1 }}</div>
        </div>
        <div class="m-chunk-src">{{ chunkSource(chunk) }}</div>
        <div class="m-chunk-text">{{ chunk.text.slice(0, 200) }}{{ chunk.text.length > 200 ? '...' : '' }}</div>
      </div>
    </div>

    <div v-if="hasMore" class="load-more">
      <van-button size="small" round plain type="primary" @click="loadMore">加载更多</van-button>
    </div>

    <!-- 底部管理栏：次级操作（清理过期/自愈重建），无待办时隐藏 -->
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
  </div>
</template>

<style scoped>
.m-vectors { padding: 8px 12px 24px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }

/* 顶部统计横幅（绿色系） */
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
.m-hero-icon {
  width: 40px;
  height: 40px;
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.m-hero-meta { flex: 1; min-width: 0; }
.m-hero-label { font-size: 12px; opacity: 0.85; }
.m-hero-value { font-size: 20px; font-weight: 700; margin-top: 2px; }
.m-hero-extra { text-align: right; flex-shrink: 0; }
.m-hero-mini { font-size: 11px; opacity: 0.92; margin-top: 2px; }
.m-hero-num { font-weight: 700; font-size: 13px; }
.m-hero-clear {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 12px;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.22);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
  transition: transform 0.1s, background 0.15s;
}
.m-hero-clear:active { transform: scale(0.95); background: rgba(255, 255, 255, 0.34); }

/* 查询卡：搜索 + 文档筛选 合一 */
.m-query-card {
  margin: 4px 0 10px;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  overflow: hidden;
}
.m-query-card :deep(.van-search) { padding: 8px 8px 2px; }
.m-query-card :deep(.van-search__content) { background: var(--m-vector-soft, rgba(16, 185, 129, 0.08)); }
.m-query-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 14px 10px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.m-query-row::-webkit-scrollbar { display: none; }
.m-filter-chip {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: var(--m-vector-soft, rgba(16, 185, 129, 0.10));
  color: var(--text-secondary, #64748b);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.15s, color 0.15s;
}
.m-filter-chip.active {
  background: var(--m-vector, #10b981);
  color: #fff;
  font-weight: 600;
}

/* 分块卡片 */
.chunk-list { display: flex; flex-direction: column; gap: 10px; }
.m-chunk-card {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  padding: 12px 14px;
}
.m-chunk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.m-chunk-chapter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--m-vector, #10b981);
  border-left: 3px solid var(--m-vector, #10b981);
  padding-left: 8px;
  line-height: 1.4;
  min-width: 0;
}
.m-chunk-idx {
  font-size: 11px;
  font-weight: 700;
  color: var(--m-vector, #10b981);
  background: var(--m-vector-soft, rgba(16, 185, 129, 0.12));
  border-radius: 999px;
  padding: 2px 8px;
  flex-shrink: 0;
}
.m-chunk-src {
  font-size: 11px;
  color: var(--text-tertiary, #94a3b8);
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-chunk-text {
  font-size: 13px;
  color: var(--text-secondary, #64748b);
  line-height: 1.6;
  word-break: break-word;
}
.load-more { display: flex; justify-content: center; padding: 16px 0; }

/* 底部管理栏（固定，次级操作） */
.m-vectors { padding-bottom: 92px; }
.m-manage-bar {
  position: sticky;
  bottom: 8px;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 0 4px;
  padding: 8px;
  background: color-mix(in srgb, var(--surface, #fff) 88%, transparent);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border, #eef1f6);
  border-radius: 18px;
  box-shadow: 0 6px 24px rgba(15, 23, 42, 0.10);
}
.m-manage-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.m-manage-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 11px;
  border: none;
  border-radius: 999px;
  background: var(--m-vector-soft, rgba(16, 185, 129, 0.12));
  color: var(--m-vector, #10b981);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s, transform 0.1s;
}
.m-manage-btn .van-icon { font-size: 13px; }
.m-manage-btn:active { transform: scale(0.96); opacity: 0.8; }
.m-manage-btn.repair {
  background: rgba(217, 119, 6, 0.12);
  color: #d97706;
}
</style>

