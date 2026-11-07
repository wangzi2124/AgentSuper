import { defineStore } from 'pinia'
import { ref } from 'vue'

export type TabKind = 'page' | 'conversation'

export interface AppTab {
  path: string
  title: string
  kind: TabKind
}

const STORAGE_KEY = 'agentsuper:app-tabs-v1'
const HOME_PATH = '/multi-agent'
const MAX_TABS = 24

function isTab(value: unknown): value is AppTab {
  if (!value || typeof value !== 'object') return false
  const tab = value as Partial<AppTab>
  return typeof tab.path === 'string'
    && typeof tab.title === 'string'
    && (tab.kind === 'page' || tab.kind === 'conversation')
    && tab.path !== HOME_PATH
    && tab.path !== '/login'
}

function loadTabs(): AppTab[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isTab).slice(0, MAX_TABS)
  } catch {
    return []
  }
}

export const useTabsStore = defineStore('tabs', () => {
  const tabs = ref<AppTab[]>(loadTabs())
  const activePath = ref('')

  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs.value)) } catch { /* ignore */ }
  }

  function openTab(path: string, title: string, kind: TabKind) {
    if (!path || path === HOME_PATH || path === '/login') return
    const idx = tabs.value.findIndex(t => t.path === path)
    if (idx >= 0) {
      const current = tabs.value[idx]!
      if (current.title !== title || current.kind !== kind) {
        tabs.value[idx] = { path, title, kind }
        persist()
      }
      return
    }
    tabs.value.push({ path, title, kind })
    if (tabs.value.length > MAX_TABS) tabs.value.shift()
    persist()
  }

  function setActive(path: string) {
    activePath.value = path === HOME_PATH ? '' : path
  }

  function renameTab(path: string, title: string) {
    const tab = tabs.value.find(t => t.path === path)
    if (!tab || !title || tab.title === title) return
    tab.title = title
    persist()
  }

  function closeTab(path: string): string | null {
    const idx = tabs.value.findIndex(t => t.path === path)
    if (idx < 0) return null
    const wasActive = activePath.value === path
    tabs.value.splice(idx, 1)
    persist()
    if (!wasActive) return null
    return tabs.value[idx]?.path || tabs.value[idx - 1]?.path || HOME_PATH
  }

  function closeAll() {
    tabs.value = []
    persist()
  }

  return { tabs, activePath, openTab, setActive, renameTab, closeTab, closeAll }
})
