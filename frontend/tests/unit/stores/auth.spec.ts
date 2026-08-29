/**
 * auth store：启动初始化（启用/禁用/有效会话/无会话）、登录/注册/登出、isLoggedIn。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  getAuthInitInfo: vi.fn(),
  loginAccount: vi.fn(),
  registerAccount: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  getAuthInitInfo: mocks.getAuthInitInfo,
  loginAccount: mocks.loginAccount,
  registerAccount: mocks.registerAccount,
  logout: mocks.logout,
}))

import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  setActivePinia(createPinia())
  mocks.getAuthInitInfo.mockReset()
  mocks.loginAccount.mockReset()
  mocks.registerAccount.mockReset()
  mocks.logout.mockReset()
})

describe('init', () => {
  it('启用 + 有效会话 → 登录态', async () => {
    mocks.getAuthInitInfo.mockResolvedValue({
      enabled: true,
      session: { user_id: 'u1', username: 'alice', account_type: 'account' },
    })
    const store = useAuthStore()
    await store.init()
    expect(store.enabled).toBe(true)
    expect(store.user_id).toBe('u1')
    expect(store.username).toBe('alice')
    expect(store.isLoggedIn).toBe(true)
  })

  it('启用但无会话 → 保持未登录（强制登录页）', async () => {
    mocks.getAuthInitInfo.mockResolvedValue({ enabled: true, session: null })
    const store = useAuthStore()
    await store.init()
    expect(store.enabled).toBe(true)
    expect(store.user_id).toBe('')
    expect(store.isLoggedIn).toBe(false)
  })

  it('未启用 → enabled=false 且未登录', async () => {
    mocks.getAuthInitInfo.mockResolvedValue({ enabled: false, session: null })
    const store = useAuthStore()
    await store.init()
    expect(store.enabled).toBe(false)
    expect(store.isLoggedIn).toBe(false)
  })

  it('init 幂等：ready 后不再重复探测', async () => {
    mocks.getAuthInitInfo.mockResolvedValue({ enabled: false, session: null })
    const store = useAuthStore()
    await store.init()
    await store.init()
    expect(mocks.getAuthInitInfo).toHaveBeenCalledTimes(1)
  })
})

describe('login / register / logout', () => {
  it('login 成功写入账号态', async () => {
    mocks.loginAccount.mockResolvedValue({ user_id: 'u9', username: 'bob' })
    const store = useAuthStore()
    store.enabled = true // 真实流程：init() 探测启用后才允许 login
    const data = await store.login('bob', 'pw')
    expect(data.user_id).toBe('u9')
    expect(store.accountType).toBe('account')
    expect(store.isLoggedIn).toBe(true)
  })

  it('register 成功写入账号态', async () => {
    mocks.registerAccount.mockResolvedValue({ user_id: 'u7', username: 'carol' })
    const store = useAuthStore()
    store.enabled = true
    await store.register('carol', 'pw')
    expect(store.user_id).toBe('u7')
    expect(store.username).toBe('carol')
  })

  it('logout 清空登录态并调本地登出', () => {
    mocks.getAuthInitInfo.mockResolvedValue({ enabled: true, session: { user_id: 'u1', username: 'a', account_type: 'device' } })
    const store = useAuthStore()
    store.user_id = 'u1'
    store.enabled = true
    store.logout()
    expect(mocks.logout).toHaveBeenCalled()
    expect(store.user_id).toBe('')
    expect(store.isLoggedIn).toBe(false)
  })
})
