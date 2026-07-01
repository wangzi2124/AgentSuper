import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Plugin } from '../types'
import { listPlugins, togglePlugin } from '../api/plugins'

export const usePluginStore = defineStore('plugins', () => {
  const plugins = ref<Plugin[]>([])
  const loading = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      plugins.value = await listPlugins()
    } finally {
      loading.value = false
    }
  }

  async function toggle(name: string, enabled: boolean) {
    await togglePlugin(name, enabled)
    const plugin = plugins.value.find((p) => p.name === name)
    if (plugin) plugin.enabled = enabled
  }

  return { plugins, loading, fetchAll, toggle }
})
