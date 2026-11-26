// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const mocks = vi.hoisted(() => ({
  ensureAuth: vi.fn<() => Promise<void>>(),
  getUserId: vi.fn(() => 'u1'),
  getAuthToken: vi.fn(() => 'tok'),
}))
vi.mock('@/api/auth', () => mocks)

import { fetchWithTimeout } from '@/api/fetch'

describe('api/fetch timeout', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.ensureAuth.mockReset()
    mocks.getUserId.mockReturnValue('u1')
    mocks.getAuthToken.mockReturnValue('tok')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('does not count auth initialization toward request timeout', async () => {
    let resolveAuth!: () => void
    mocks.ensureAuth.mockImplementation(() => new Promise<void>((resolve) => { resolveAuth = resolve }))

    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect((init?.signal as AbortSignal).aborted).toBe(false)
      return { ok: true, status: 200 } as Response
    })
    vi.stubGlobal('fetch', fetchMock)

    const pending = fetchWithTimeout('/api/sessions', { method: 'GET' }, 100)
    await vi.advanceTimersByTimeAsync(150)
    expect(fetchMock).not.toHaveBeenCalled()

    resolveAuth()
    await vi.advanceTimersByTimeAsync(0)
    await expect(pending).resolves.toMatchObject({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('still aborts slow requests after auth completes', async () => {
    mocks.ensureAuth.mockResolvedValue(undefined)
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      (init?.signal as AbortSignal).addEventListener('abort', () => {
        reject((init?.signal as AbortSignal & { reason?: unknown }).reason)
      })
    }))
    vi.stubGlobal('fetch', fetchMock)

    const pending = fetchWithTimeout('/api/sessions', { method: 'GET' }, 100)
    const assertion = expect(pending).rejects.toMatchObject({ name: 'TimeoutError', message: 'timeout after 100ms' })
    await vi.advanceTimersByTimeAsync(100)
    await assertion
  })
})
