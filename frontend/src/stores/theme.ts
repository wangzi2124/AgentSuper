import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'agent_super_theme'

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

function applyTheme(dark: boolean) {
  const root = document.documentElement
  if (dark) root.setAttribute('data-theme', 'dark')
  else root.removeAttribute('data-theme')
}

// 浅色 / 深色主题切换（持久化到 localStorage，默认跟随系统偏好）
export const useThemeStore = defineStore('theme', () => {
  const isDark = ref(false)

  function init() {
    let saved: string | null = null
    try { saved = localStorage.getItem(STORAGE_KEY) } catch { /* noop */ }
    isDark.value = saved === 'dark' ? true : (saved === 'light' ? false : systemPrefersDark())
    applyTheme(isDark.value)
  }

  function toggle() {
    isDark.value = !isDark.value
    try { localStorage.setItem(STORAGE_KEY, isDark.value ? 'dark' : 'light') } catch { /* noop */ }
    applyTheme(isDark.value)
  }

  return { isDark, init, toggle }
})
