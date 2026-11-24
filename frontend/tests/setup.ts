/**
 * Vitest 全局 setup：jsdom 环境补丁（matchMedia / crypto.randomUUID）、
 * IndexedDB 注入。
 */
import 'fake-indexeddb/auto'

if (typeof localStorage === 'undefined') {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, String(value)) },
      removeItem: (key: string) => { values.delete(key) },
      clear: () => values.clear(),
      key: (index: number) => [...values.keys()][index] ?? null,
      get length() { return values.size },
    } satisfies Storage,
  })
}

// jsdom 缺 window.matchMedia（MobileShell 用 window.matchMedia('(max-width: 768px)')）
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// 兜底 crypto.randomUUID（multiAgent store genId 依赖；新版 jsdom 自带）
if (typeof crypto !== 'undefined' && !crypto.randomUUID) {
  Object.defineProperty(crypto, 'randomUUID', {
    value: () => '00000000-0000-4000-8000-000000000000',
  })
}
