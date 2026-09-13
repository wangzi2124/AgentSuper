/**
 * permission store：pending 请求倒计时到期自动拒绝（respond denied）。
 * 覆盖 404（后端已过期）静默处理 与 乐观移除，防止 stale 请求卡死审批面板。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  respondToRequest: vi.fn(),
  fetchPendingRequests: vi.fn().mockResolvedValue({ pending: [] }),
  fetchWorkspaces: vi.fn().mockResolvedValue({ workspaces: [] }),
  addWorkspace: vi.fn(),
  removeWorkspace: vi.fn(),
}))

vi.mock('@/api/permission', () => ({
  respondToRequest: mocks.respondToRequest,
  fetchPendingRequests: mocks.fetchPendingRequests,
  fetchWorkspaces: mocks.fetchWorkspaces,
  addWorkspace: mocks.addWorkspace,
  removeWorkspace: mocks.removeWorkspace,
}))

import { usePermissionStore } from '@/stores/permission'
import type { PermissionRequest } from '@/types'

function req(id: string, expiresInSec: number): PermissionRequest {
  const now = Date.now()
  return {
    id,
    path: '/tmp/x.txt',
    operation: 'write',
    tool_name: 'tool_write_file',
    tool_args: {},
    created_at: new Date(now).toISOString(),
    expires_at: new Date(now + expiresInSec * 1000).toISOString(),
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  mocks.respondToRequest.mockReset().mockResolvedValue(undefined)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('countdown auto-deny', () => {
  it('请求到期自动拒绝并移除，无需人工干涉', async () => {
    const store = usePermissionStore()
    // expires 3s 后截止；留 2s 提前量 → 第 1s 后触发
    store.handleIncoming(req('r1', 3))

    expect(store.pendingRequests).toHaveLength(1)
    expect(mocks.respondToRequest).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1500)
    expect(mocks.respondToRequest).toHaveBeenCalledTimes(1)
    expect(mocks.respondToRequest).toHaveBeenCalledWith('r1', 'denied', false)
    expect(store.pendingRequests).toHaveLength(0)
  })

  it('后端已过期（respond 404）时自动拒绝静默成功，列表不残留', async () => {
    mocks.respondToRequest.mockRejectedValueOnce(new Error('404: Request not found'))
    const store = usePermissionStore()
    store.handleIncoming(req('r2', 3))

    await vi.advanceTimersByTimeAsync(1500)
    // 不吞掉异常 → handleIncoming 处未使用 await，这里等微任务清空
    await vi.advanceTimersByTimeAsync(0)

    expect(store.pendingRequests).toHaveLength(0)
  })

  it('手动 respond 也乐观移除，当时后端 404 不抛错', async () => {
    mocks.respondToRequest.mockRejectedValueOnce(new Error('404'))
    const store = usePermissionStore()
    store.handleIncoming(req('r3', 3600))
    await expect(store.respond('r3', 'allowed', false)).resolves.toBeUndefined()
    expect(store.pendingRequests).toHaveLength(0)
  })

  it('列表清空后倒计时器停止（不再触发重复拒绝）', async () => {
    const store = usePermissionStore()
    store.handleIncoming(req('r4', 3))
    store.respond('r4', 'allowed')

    await vi.advanceTimersByTimeAsync(2000)
    // respond 已清空并移除，到期不再触发（respondToRequest 仅手动调用过一次）
    expect(mocks.respondToRequest).toHaveBeenCalledTimes(1)
    expect(store.pendingRequests).toHaveLength(0)
  })
})