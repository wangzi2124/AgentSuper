import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Chunk } from '../types'
import { listChunks } from '../api/vectors'

export const useVectorStore = defineStore('vectors', () => {
  const chunks = ref<Chunk[]>([])
  const total = ref(0)
  const offset = ref(0)
  const limit = ref(50)
  const loading = ref(false)
  const filterDocId = ref('')
  const searchQuery = ref('')

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

  async function loadMore() {
    if (loading.value || chunks.value.length >= total.value) return
    offset.value += limit.value
    await fetch(true)
  }

  function reset(docId = '', query = '') {
    filterDocId.value = docId
    searchQuery.value = query
    offset.value = 0
    chunks.value = []
    total.value = 0
    fetch()
  }

  return { chunks, total, offset, limit, loading, filterDocId, searchQuery, fetch, loadMore, reset }
})
