// 自定义工具 Store [PATCH6]
import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { CustomToolItem } from '../types/customTools'
import {
  listCustomTools,
  createScriptTool,
  toggleCustomTool,
  deleteCustomTool,
} from '../api/customTools'

// 自定义工具管理 Store
export const useCustomToolsStore = defineStore('customTools', () => {
  // 自定义工具列表（脚本型）
  const items = ref<CustomToolItem[]>([])
  // 加载状态
  const loading = ref(false)

  // 获取自定义工具列表
  async function fetchAll() {
    loading.value = true
    try {
      items.value = await listCustomTools()
    } finally {
      loading.value = false
    }
  }

  // 创建脚本型工具
  async function createScript(payload: { name: string; description?: string; script: string; enabled?: boolean }) {
    const item = await createScriptTool(payload)
    await fetchAll()
    return item
  }

  // 切换启用/禁用
  async function toggle(name: string, enabled: boolean) {
    await toggleCustomTool(name, enabled)
    const it = items.value.find((i) => i.name === name)
    if (it) it.enabled = enabled
  }

  // 删除
  async function remove(name: string) {
    await deleteCustomTool(name)
    items.value = items.value.filter((i) => i.name !== name)
  }

  return { items, loading, fetchAll, createScript, toggle, remove }
})
