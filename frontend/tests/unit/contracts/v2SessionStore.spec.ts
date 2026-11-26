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
import type { InboxInfo } from '../../../src/contracts/v2'
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

  it('hydrates persisted plan snapshot', async () => {
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
})
