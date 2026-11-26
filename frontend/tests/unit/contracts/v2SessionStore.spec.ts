import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../../src/api/v2', () => ({
  getSession: vi.fn(),
  getSessionPlan: vi.fn(),
  listForms: vi.fn(),
  listInbox: vi.fn(),
  listMessages: vi.fn(),
  listPermissions: vi.fn(),
  prompt: vi.fn(),
}))

vi.mock('../../../src/v2/eventStream', () => ({
  getV2EventStream: () => ({
    subscribe: () => () => {},
    onConnected: () => () => {},
    start: () => {},
    stop: () => {},
  }),
}))

import {
  getSession,
  getSessionPlan,
  listForms,
  listInbox,
  listMessages,
  listPermissions,
} from '../../../src/api/v2'
import type { FormInfo, InboxInfo } from '../../../src/contracts/v2'
import { useV2SessionStore } from '../../../src/v2/sessionStore'

function inboxItem(delivery: 'steer' | 'queue'): InboxInfo {
  return {
    id: 'in1',
    session_id: 's1',
    item: { type: 'user', payload: { text: 'hi' }, delivery },
    status: 'pending',
    admitted_seq: 1,
    time_created: 1,
  }
}

describe('v2 session store inbox actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getSession).mockResolvedValue({ id: 's1', status: 'idle' } as never)
    vi.mocked(listMessages).mockResolvedValue({ data: [] } as never)
    vi.mocked(listInbox).mockResolvedValue([inboxItem('steer')] as never)
    vi.mocked(listPermissions).mockResolvedValue([] as never)
    vi.mocked(listForms).mockResolvedValue([] as never)
    vi.mocked(getSessionPlan).mockResolvedValue(null)
  })

  it('applies optimistic delivery change and cancel locally', async () => {
    const store = useV2SessionStore()
    await store.hydrate('s1')
    expect(store.getRuntime('s1')?.inbox[0]?.item.delivery).toBe('steer')

    store.setInbox('s1', inboxItem('queue'))
    expect(store.getRuntime('s1')?.inbox[0]?.item.delivery).toBe('queue')

    store.removeInbox('s1', 'in1')
    expect(store.getRuntime('s1')?.inbox).toEqual([])
  })

  it('keeps the execution error visible after failure hydration', async () => {
    const store = useV2SessionStore()
    await store.hydrate('s1')
    const callsBefore = vi.mocked(getSession).mock.calls.length
    store.apply({
      id: 'fail1',
      created: Date.now(),
      type: 'session.execution.failed',
      data: {
        session_id: 's1',
        error: {
          type: 'execution_error',
          message: 'OpenAI 账户额度已用尽：请前往 billing 充值后重试。',
        },
      },
    } as never)
    expect(store.getRuntime('s1')?.error?.message).toContain('额度已用尽')
    await vi.waitFor(() => {
      expect(vi.mocked(getSession).mock.calls.length).toBeGreaterThan(callsBefore)
    })
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(store.getRuntime('s1')?.error?.message).toContain('额度已用尽')
  })

  it('auto-dismisses the execution error after 15 seconds and resets on a new failure', async () => {
    const failure = (id: string) => ({
      id,
      created: Date.now(),
      type: 'session.execution.failed',
      data: {
        session_id: 's1',
        error: { type: 'execution_error', message: 'OpenAI 账户额度已用尽，请充值。' },
      },
    } as never)
    vi.useFakeTimers()
    try {
      const store = useV2SessionStore()
      await store.hydrate('s1')
      store.apply(failure('fail_a'))
      expect(store.getRuntime('s1')?.error?.message).toContain('额度已用尽')

      await vi.advanceTimersByTimeAsync(14_000)
      store.apply(failure('fail_b'))
      await vi.advanceTimersByTimeAsync(14_000)
      expect(store.getRuntime('s1')?.error?.message).toContain('额度已用尽')

      await vi.advanceTimersByTimeAsync(1_000)
      expect(store.getRuntime('s1')?.error).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('hydrates persisted plan snapshot for a running session', async () => {
    vi.mocked(getSession).mockResolvedValue({ id: 's1', status: 'busy' } as never)
    vi.mocked(getSessionPlan).mockResolvedValue({
      session_id: 's1',
      plan_id: 'plan_1',
      revision: 4,
      tier: 'serial',
      steps: [{ id: 's1', title: '读取文件', status: 'completed' }],
    })
    const store = useV2SessionStore()
    await store.hydrate('s1')
    expect(store.getRuntime('s1')?.plan).toEqual({
      plan_id: 'plan_1',
      revision: 4,
      tier: 'serial',
      steps: [{ id: 's1', title: '读取文件', status: 'completed' }],
    })
  })

  it('ignores a persisted plan snapshot when the session is idle', async () => {
    vi.mocked(getSessionPlan).mockResolvedValue({
      session_id: 's1',
      plan_id: 'plan_1',
      revision: 4,
      tier: 'serial',
      steps: [{ id: 's1', title: '读取文件', status: 'pending' }],
    })
    const store = useV2SessionStore()
    await store.hydrate('s1')
    expect(store.getRuntime('s1')?.plan).toBeNull()
  })

  it('hydrates only pending forms and can remove them locally', async () => {
    vi.mocked(listForms).mockResolvedValue([
      { id: 'old', session_id: 's1', title: 'old', fields: [], state: 'replied', response: {} },
      { id: 'live', session_id: 's1', title: 'live', fields: [], state: 'pending', response: {} },
    ] as FormInfo[])
    const store = useV2SessionStore()
    await store.hydrate('s1')
    expect(listForms).toHaveBeenCalledWith('s1', 'pending')
    expect(store.getRuntime('s1')?.forms.map((form) => form.id)).toEqual(['live'])

    store.removeForm('s1', 'live')
    expect(store.getRuntime('s1')?.forms).toEqual([])
  })
})
