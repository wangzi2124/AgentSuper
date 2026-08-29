import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Chunk } from '../types'
import { listChunks, getVectorConfig, clearVectors, clearExpiredVectors, repairVectors, type VectorStoreConfig } from '../api/vectors'

// 向量存储管理 Store
export const useVectorStore = defineStore('vectors', () => {
  // 块列表
  const chunks = ref<Chunk[]>([])
  // 总记录数
  const total = ref(0)
  // 分页偏移量
  const offset = ref(0)
  // 每页数量
  const limit = ref(50)
  // 加载状态
  const loading = ref(false)
  // 文档 ID 筛选
  const filterDocId = ref('')
  // 搜索关键词
  const searchQuery = ref('')
  // 清理配置
  const config = ref<VectorStoreConfig | null>(null)

  // 获取分块数据，支持追加模式
  async function fetch(append = false) {
    loading.value = true
    try {
      const res = await listChunks(
        offset.value, limit.value,
        filterDocId.value || undefined,
        searchQuery.value || undefined,
      )
      chunks.value = append ? [...chunks.value, ...res.chunks] : res.chunks
      total.value = res.total
    } finally {
      loading.value = false
    }
  }

  // 重置筛选条件并重新加载
  function reset(docId = '', query = '') {
    filterDocId.value = docId
    searchQuery.value = query
    offset.value = 0
    chunks.value = []
    total.value = 0
    fetch()
  }

  // 加载清理配置
  async function loadConfig() {
    try {
      config.value = await getVectorConfig()
    } catch {
      config.value = null
    }
  }

  // 清空全部知识库数据
  async function clearAll() {
    await clearVectors()
    chunks.value = []
    total.value = 0
    offset.value = 0
    await loadConfig()
    await fetch()
  }

  // 手动触发 TTL 过期清理
  async function clearExpired() {
    const res = await clearExpiredVectors()
    await loadConfig()
    await fetch()
    return res
  }

  // [D2] 自愈重建：补齐 index_state != ready 文档的派生索引（向量/BM25/章节）
  async function repair() {
    const res = await repairVectors()
    await loadConfig()
    await fetch()
    return res
  }

  return { chunks, total, offset, limit, loading, filterDocId, searchQuery, config, fetch, reset, loadConfig, clearAll, clearExpired, repair }
})
