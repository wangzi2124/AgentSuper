import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { GeneratedFile } from '../types'
import { listGenerated, deleteGenerated } from '../api/generated'

// 生成文件管理 Store
export const useGeneratedStore = defineStore('generated', () => {
  // 生成文件列表
  const files = ref<GeneratedFile[]>([])
  // 加载状态
  const loading = ref(false)
  // 搜索关键词
  const searchQuery = ref('')

  // 根据搜索关键词过滤文件列表
  const filteredFiles = computed(() => {
    if (!searchQuery.value) return files.value
    const q = searchQuery.value.toLowerCase()
    return files.value.filter(f => f.filename.toLowerCase().includes(q))
  })

  // 获取所有生成文件
  async function fetchAll() {
    loading.value = true
    try {
      const res = await listGenerated()
      files.value = res.files
    } finally {
      loading.value = false
    }
  }

  // 删除指定生成文件
  async function remove(filename: string) {
    await deleteGenerated(filename)
    files.value = files.value.filter(f => f.filename !== filename)
  }

  // 格式化文件大小为可读字符串
  function formatSize(bytes: number | undefined | null): string {
    if (bytes === undefined || bytes === null || isNaN(bytes)) return '未知'
    if (bytes === 0) return '0 B'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return { files, loading, searchQuery, filteredFiles, fetchAll, remove, formatSize }
})
