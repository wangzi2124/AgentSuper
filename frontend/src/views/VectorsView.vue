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
  vs.loadConfig()
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

// 清空全部知识库数据（向量库 + 章节库 + BM25 + 上传文件）
async function handleClearAll() {
  const confirmMsg = '确定要清空全部知识库数据吗？\n\n将删除：\n- 向量库全部分块\n- 章节库全部记录\n- BM25 索引\n- 全部上传文件\n\n此操作不可撤销！'
  if (!confirm(confirmMsg)) return
  try {
    await vs.clearAll()
    ds.fetchAll()
    alert('知识库数据已全部清空')
  } catch (e: any) {
    alert(`清空失败：${e.message || e}`)
  }
}

// 手动触发 TTL 过期清理
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
</script>

<template>
  <div class="page-header">
    <h2>向量库</h2>
    <p>浏览和搜索存储在向量数据库中的分块</p>
  </div>
  <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;align-items:center;">
    <div style="display:flex;gap:8px;align-items:center;margin-left:auto;">
      <template v-if="vs.config">
        <span style="font-size:12px;color:var(--text-secondary);">
          TTL: {{ vs.config.ttl_days > 0 ? vs.config.ttl_days + ' 天' : '未启用' }}
          <template v-if="vs.config.ttl_days > 0">· 每 {{ vs.config.cleanup_interval_hours }}h 检查</template>
        </span>
        <button v-if="vs.config.ttl_days > 0" class="btn" @click="handleClearExpired">清理过期</button>
      </template>
      <button class="btn btn-danger" @click="handleClearAll">清空向量库</button>
    </div>
  </div>
  <div class="page-content">
    <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
      <select v-model="selectedDocId" @change="onFilterChange" style="flex:1;min-width:180px;padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;">
        <option value="">全部文档</option>
        <option v-for="doc in ds.documents" :key="doc.id" :value="doc.id">{{ doc.filename }} ({{ doc.chunk_count }} chunks)</option>
      </select>
      <div style="display:flex;gap:4px;flex:2;min-width:200px;">
        <input
          v-model="searchText"
          type="text"
          placeholder="搜索分块内容..."
          style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;"
          @keyup.enter="onSearch"
        />
        <button class="btn" @click="onSearch">搜索</button>
        <button v-if="vs.searchQuery" class="btn" @click="searchText = ''; onSearch()">清除</button>
      </div>
    </div>

    <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">
      {{ vs.total }} 个分块
      <template v-if="vs.searchQuery"> — 匹配 "{{ vs.searchQuery }}"</template>
      <template v-if="vs.chunks.length > 0"> — 第 {{ currentPage }} / {{ totalPages }} 页</template>
    </div>

    <div v-if="vs.loading && vs.chunks.length === 0" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="vs.chunks.length === 0" class="empty-state">
      <div class="icon">📦</div>
      <p>未找到分块</p>
      <p style="font-size:13px;margin-top:4px;">请先上传文档以填充向量库。</p>
    </div>

    <div v-else>
      <div style="border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead>
            <tr style="background:var(--bg);text-align:left;">
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;white-space:nowrap;">#</th>
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;">来源</th>
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;">章节</th>
              <th style="padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600;">内容</th>
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
        <button class="btn" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">上一页</button>
        <span style="font-size:13px;color:var(--text-secondary);">
          <select :value="currentPage" @change="goToPage(Number(($event.target as HTMLSelectElement).value))" style="padding:4px 6px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;">
            <option v-for="p in totalPages" :key="p" :value="p">第 {{ p }} 页</option>
          </select>
          / {{ totalPages }}
        </span>
        <button class="btn" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">下一页</button>
      </div>
    </div>
  </div>
</template>
