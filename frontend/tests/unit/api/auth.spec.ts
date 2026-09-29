// @vitest-environment jsdom
// `@/api/auth` 的 localStorage 会话读写回归。
// 重点 [D7]：logout() 必须 **删除** 键，而不是写空串 —— 否则共享设备上残留
// `key=""` 残骸（getUsername() 会回落到 userId、expires_at 的 '0' 可能被误解析）。
import { describe, it, expect, beforeEach, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }))
vi.mock('@/api/errors', () => ({ apiRequest: mocks.apiRequest }))

import {
  loginAccount,
  logout,
  getUserId,
  getUsername,
  hasStoredSession,
} from '@/api/auth'

const KEYS = [
  'agent_super_user_id',
  'agent_super_username',
  'agent_super_account_type',
  'agent_super_auth_token',
  'agent_super_auth_token_expires_at',
]

function seedSession(): void {
  localStorage.setItem('agent_super_user_id', 'u1')
  localStorage.setItem('agent_super_username', 'alice')
  localStorage.setItem('agent_super_account_type', 'account')
  localStorage.setItem('agent_super_auth_token', 'tok-abc')
  localStorage.setItem('agent_super_auth_token_expires_at', String(Math.floor(Date.now() / 1000) + 3600))
}

describe('api/auth localStorage 会话', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.apiRequest.mockReset()
  })

  it('login 写入全部 5 个会话键', async () => {
    const expires = Math.floor(Date.now() / 1000) + 3600
    mocks.apiRequest.mockResolvedValue({
      user_id: 'u1',
      username: 'alice',
      account_type: 'account',
      token: 'tok-abc',
      expires_at: expires,
    })
    await loginAccount('alice', 'pw')

    expect(getUserId()).toBe('u1')
    expect(getUsername()).toBe('alice')
    expect(localStorage.getItem('agent_super_auth_token')).toBe('tok-abc')
    expect(localStorage.getItem('agent_super_auth_token_expires_at')).toBe(String(expires))
    expect(hasStoredSession()).toBe(true)
  })

  it('[D7] logout 删除全部会话键，而不是写空串', async () => {
    seedSession()
    expect(hasStoredSession()).toBe(true)

    logout()

    for (const key of KEYS) {
      expect(localStorage.getItem(key)).toBeNull()
    }
    // 写空串的旧实现会在这里失败（值为 '' 而非 null）
    expect(hasStoredSession()).toBe(false)
    expect(getUserId()).toBe('anonymous')
  })

  it('[D7] logout 后 getUsername 不残留上一个用户的名字', () => {
    seedSession()
    logout()
    // 空串实现下 getUsername() 读到 '' 会回落到 getUserId()='anonymous'；
    // 删除实现下同样回落，但用户名键必须真的不存在，避免共享设备泄露旧身份
    expect(localStorage.getItem('agent_super_username')).toBeNull()
    expect(getUsername()).toBe('anonymous')
  })
})
