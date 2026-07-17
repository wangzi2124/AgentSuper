import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { GeneratedFile } from '../types'
import { listGenerated, deleteGenerated } from '../api/generated'

export const useGeneratedStore = defineStore('generated', () => {
  const files = ref<GeneratedFile[]>([])
  const loading = ref(false)
  const searchQuery = ref('')

  const filteredFiles = computed(() => {
    if (!searchQuery.value) return files.value
    const q = searchQuery.value.toLowerCase()
    return files.value.filter(f => f.filename.toLowerCase().includes(q))
  })

  async function fetchAll() {
    loading.value = true
    try {
      const res = await listGenerated()
      files.value = res.files
    } finally {
      loading.value = false
    }
  }

  async function remove(filename: string) {
    await deleteGenerated(filename)
    files.value = files.value.filter(f => f.filename !== filename)
  }

  function formatSize(bytes: number | undefined | null): string {
    if (bytes === undefined || bytes === null || isNaN(bytes)) return '未知'
    if (bytes === 0) return '0 B'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return { files, loading, searchQuery, filteredFiles, fetchAll, remove, formatSize }
})
