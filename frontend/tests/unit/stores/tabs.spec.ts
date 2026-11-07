import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTabsStore } from '@/stores/tabs'

describe('tabs store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('ignores home and login routes', () => {
    const tabs = useTabsStore()
    tabs.openTab('/multi-agent', '主页', 'page')
    tabs.openTab('/login', '登录', 'page')
    expect(tabs.tabs).toEqual([])
  })

  it('dedupes tabs and refreshes titles', () => {
    const tabs = useTabsStore()
    tabs.openTab('/documents', '文档管理', 'page')
    tabs.openTab('/multi-agent/abc', '会话', 'conversation')
    tabs.openTab('/multi-agent/abc', '真实标题', 'conversation')
    expect(tabs.tabs).toHaveLength(2)
    expect(tabs.tabs[1]?.title).toBe('真实标题')
  })

  it('restores tabs from localStorage', () => {
    const tabs = useTabsStore()
    tabs.openTab('/documents', '文档管理', 'page')
    setActivePinia(createPinia())
    const restored = useTabsStore()
    expect(restored.tabs).toEqual([{ path: '/documents', title: '文档管理', kind: 'page' }])
  })

  it('returns adjacent tab when closing the active one', () => {
    const tabs = useTabsStore()
    tabs.openTab('/a', 'A', 'page')
    tabs.openTab('/b', 'B', 'page')
    tabs.setActive('/b')
    expect(tabs.closeTab('/b')).toBe('/a')
    expect(tabs.tabs.map(t => t.path)).toEqual(['/a'])
    tabs.setActive('/a')
    expect(tabs.closeTab('/a')).toBe('/multi-agent')
  })

  it('does not navigate when closing an inactive tab', () => {
    const tabs = useTabsStore()
    tabs.openTab('/a', 'A', 'page')
    tabs.openTab('/b', 'B', 'page')
    tabs.setActive('/b')
    expect(tabs.closeTab('/a')).toBeNull()
    expect(tabs.activePath).toBe('/b')
  })
})
