import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSessionModelStore, SESSION_MODEL_DRAFT_KEY } from '@/stores/sessionModel'

const STORAGE_KEY = 'agentsuper:session-model:v1'

describe('sessionModel store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('desired 持久化到 localStorage，active 不持久化', () => {
    const store = useSessionModelStore()
    store.select('s1', 'model-a')
    store.setActive('s1', 'model-b')

    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
    expect(raw).toEqual({ version: 1, desired: { s1: 'model-a' } })
    expect(store.active('s1')).toBe('model-b')
  })

  it('revert 删除 desired，读取回落到 active', () => {
    const store = useSessionModelStore()
    store.setActive('s1', 'model-a')
    store.select('s1', 'model-b')
    expect(store.desired('s1')).toBe('model-b')

    store.revert('s1')
    expect(store.desired('s1')).toBeUndefined()
    expect(store.active('s1')).toBe('model-a')
  })

  it('copy 在本地 key 迁移到服务端 id 时保留偏好', () => {
    const store = useSessionModelStore()
    store.select('local-1', 'model-a')
    store.setActive('local-1', 'model-a')

    store.copy('local-1', 'server-1')
    expect(store.desired('server-1')).toBe('model-a')
    expect(store.active('server-1')).toBe('model-a')
  })

  it('copy 不覆盖服务端 key 已有的 desired', () => {
    const store = useSessionModelStore()
    store.select('local-1', 'model-a')
    store.select('server-1', 'model-b')

    store.copy('local-1', 'server-1')
    expect(store.desired('server-1')).toBe('model-b')
  })

  it('forget 清理指定会话的 desired/active', () => {
    const store = useSessionModelStore()
    store.select('s1', 'model-a')
    store.setActive('s1', 'model-a')

    store.forget('s1')
    expect(store.desired('s1')).toBeUndefined()
    expect(store.active('s1')).toBeUndefined()
  })

  it('draft key 用于未落库的新会话', () => {
    const store = useSessionModelStore()
    store.select(SESSION_MODEL_DRAFT_KEY, 'model-draft')
    expect(store.desired()).toBe('model-draft')
  })

  it('忽略损坏或版本不匹配的本地缓存', () => {
    localStorage.setItem(STORAGE_KEY, '{bad json')
    expect(useSessionModelStore().desired('s1')).toBeUndefined()

    setActivePinia(createPinia())
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 99, desired: { s1: 'x' } }))
    expect(useSessionModelStore().desired('s1')).toBeUndefined()
  })
})
