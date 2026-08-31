import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'agent_super_theme'
const BG_STORAGE_KEY = 'agent_super_bg'

// 背景色预设（value 对应 <html data-bg="...">）
export interface BgVariant {
  value: string
  label: string
  swatch: string
  glowSwatch?: string
  dark: boolean
}

export const BG_VARIANTS: BgVariant[] = [
  { value: 'deep-space', label: '深空黑', swatch: '#0a0e1a', dark: true },
  { value: 'slate', label: '石墨灰', swatch: '#0f1722', dark: true },
  { value: 'violet', label: '暮紫', swatch: '#14102a', glowSwatch: '#7c6cf0', dark: true },
  { value: 'cyan', label: '青蓝', swatch: '#082030', glowSwatch: '#22d3ee', dark: true },
  { value: 'emerald', label: '墨绿', swatch: '#041f18', glowSwatch: '#34d399', dark: true },
  { value: 'rose', label: '绛紫红', swatch: '#1d0a16', glowSwatch: '#fb7185', dark: true },
  { value: 'amber', label: '暖金', swatch: '#1c1307', glowSwatch: '#fbbf24', dark: true },
  { value: 'light', label: '素白', swatch: '#f8fafc', dark: false },
]

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

function applyBg(variant: string) {
  const root = document.documentElement
  root.setAttribute('data-bg', variant)
}

// 浅色 / 深色主题 + 背景色（持久化到 localStorage，默认跟随系统偏好）
export const useThemeStore = defineStore('theme', () => {
  const isDark = ref(false)
  const bgVariant = ref('deep-space')

  function init() {
    let saved: string | null = null
    try { saved = localStorage.getItem(STORAGE_KEY) } catch { /* noop */ }
    isDark.value = saved === 'dark' ? true : (saved === 'light' ? false : systemPrefersDark())
    applyTheme(isDark.value)

    let bgSaved: string | null = null
    try { bgSaved = localStorage.getItem(BG_STORAGE_KEY) } catch { /* noop */ }
    bgVariant.value = BG_VARIANTS.some(v => v.value === bgSaved) ? (bgSaved as string) : 'deep-space'
    applyBg(bgVariant.value)
  }

  function toggle() {
    isDark.value = !isDark.value
    try { localStorage.setItem(STORAGE_KEY, isDark.value ? 'dark' : 'light') } catch { /* noop */ }
    applyTheme(isDark.value)
  }

  function setBg(variant: string) {
    const v = BG_VARIANTS.find(x => x.value === variant) ?? BG_VARIANTS[0]
    bgVariant.value = v.value
    try { localStorage.setItem(BG_STORAGE_KEY, v.value) } catch { /* noop */ }
    applyBg(v.value)
    // 背景色与明暗主题联动：深色背景 → 深色模式，素白 → 浅色模式
    if (v.dark !== isDark.value) toggle()
  }

  return { isDark, bgVariant, init, toggle, setBg }
})
