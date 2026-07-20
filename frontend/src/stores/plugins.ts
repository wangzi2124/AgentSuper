import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Plugin } from '../types'
import { listPlugins, togglePlugin } from '../api/plugins'

// 插件管理 Store
export const usePluginStore = defineStore('plugins', () => {
  // 插件列表
  const plugins = ref<Plugin[]>([])
  // 加载状态
  const loading = ref(false)

  // 获取所有插件
  async function fetchAll() {
    loading.value = true
    try {
      plugins.value = await listPlugins()
    } finally {
      loading.value = false
    }
  }

  // 切换插件的启用状态
  async function toggle(name: string, enabled: boolean) {
    await togglePlugin(name, enabled)
    const plugin = plugins.value.find((p) => p.name === name)
    if (plugin) plugin.enabled = enabled
  }

  return { plugins, loading, fetchAll, toggle }
})
