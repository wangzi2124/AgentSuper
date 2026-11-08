export interface CustomTheme {
  name: string
  dark: boolean
  bg?: string
  tokens: Record<string, string>
}

export interface ThemeValidation {
  theme?: CustomTheme
  error?: string
}

const TOKEN_KEY = /^--[a-z0-9-]+$/i

export function validateTheme(raw: unknown): ThemeValidation {
  if (!raw || typeof raw !== 'object') return { error: '主题必须是对象' }
  const value = raw as Record<string, unknown>
  const name = String(value.name || '').trim()
  if (!name) return { error: '主题缺少 name' }
  const tokens = value.tokens
  if (!tokens || typeof tokens !== 'object') return { error: '主题缺少 tokens' }
  const clean: Record<string, string> = {}
  for (const [key, item] of Object.entries(tokens as Record<string, unknown>)) {
    if (!TOKEN_KEY.test(key)) return { error: `非法 token 名：${key}` }
    clean[key] = String(item)
  }
  return {
    theme: {
      name,
      dark: Boolean(value.dark),
      bg: value.bg ? String(value.bg) : undefined,
      tokens: clean,
    },
  }
}

export function parseHex(value: string): [number, number, number] | null {
  const text = value.trim()
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text)
  if (!match) return null
  const hex = match[1].length === 3 ? match[1].split('').map((c) => c + c).join('') : match[1]
  return [
    parseInt(hex.slice(0, 2), 16),
    parseInt(hex.slice(2, 4), 16),
    parseInt(hex.slice(4, 6), 16),
  ]
}

function luminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrastRatio(foreground: string, background: string): number {
  const fg = parseHex(foreground)
  const bg = parseHex(background)
  if (!fg || !bg) return 0
  const a = luminance(fg)
  const b = luminance(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

export function checkContrast(theme: CustomTheme): string[] {
  const text = theme.tokens['--text']
  const bg = theme.tokens['--bg'] || theme.bg
  if (!text || !bg) return []
  const ratio = contrastRatio(text, bg)
  return ratio > 0 && ratio < 4.5 ? [`正文与背景对比度偏低：${ratio.toFixed(2)}:1`] : []
}

export function applyCustomTheme(
  theme: CustomTheme,
  root: HTMLElement = document.documentElement,
): void {
  if (theme.dark) root.setAttribute('data-theme', 'dark')
  else root.removeAttribute('data-theme')
  if (theme.bg) root.setAttribute('data-bg', theme.bg)
  for (const [key, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(key, value)
  }
}

export function clearCustomTheme(root: HTMLElement = document.documentElement): void {
  for (const key of Array.from(root.style)) {
    if (key.startsWith('--')) root.style.removeProperty(key)
  }
}

const STORAGE_KEY = 'agentsuper:custom-themes-v1'
const ACTIVE_KEY = 'agentsuper:custom-theme-active-v1'

export function listCustomThemes(): CustomTheme[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    return raw
      .map((item) => validateTheme(item).theme)
      .filter((item): item is CustomTheme => Boolean(item))
  } catch {
    return []
  }
}

export function saveCustomTheme(theme: CustomTheme): void {
  const items = listCustomThemes().filter((item) => item.name !== theme.name)
  items.push(theme)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
    localStorage.setItem(ACTIVE_KEY, theme.name)
  } catch {
    /* storage may be disabled */
  }
}

export function removeCustomTheme(name: string): void {
  try {
    const items = listCustomThemes().filter((item) => item.name !== name)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  } catch {
    /* noop */
  }
}

export function loadActiveCustomTheme(): CustomTheme | null {
  try {
    const name = localStorage.getItem(ACTIVE_KEY)
    if (!name) return null
    return listCustomThemes().find((item) => item.name === name) || null
  } catch {
    return null
  }
}
