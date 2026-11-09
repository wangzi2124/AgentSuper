import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'agentsuper:sidebar-collapsed'

export const useSidebarStore = defineStore('sidebar', () => {
  const collapsed = ref(false)
  try { collapsed.value = localStorage.getItem(STORAGE_KEY) === '1' } catch { /* ignore */ }

  function toggle() {
    collapsed.value = !collapsed.value
    try { localStorage.setItem(STORAGE_KEY, collapsed.value ? '1' : '0') } catch { /* ignore */ }
  }

  return { collapsed, toggle }
})
