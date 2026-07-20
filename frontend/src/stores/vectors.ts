import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Chunk } from '../types'
import { listChunks } from '../api/vectors'

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

  return { chunks, total, offset, limit, loading, filterDocId, searchQuery, fetch, reset }
})
