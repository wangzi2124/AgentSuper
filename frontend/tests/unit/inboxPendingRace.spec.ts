import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { InboxInfo, PromptInput, V2Event } from '@/contracts/v2'

const { handlers } = vi.hoisted(() => ({
  handlers: [] as Array<(event: V2Event) => void>,
}))

vi.mock('@/api/v2', () => ({
  getSession: vi.fn(),
  getSessionPlan: vi.fn(),
  listForms: vi.fn(),
  listInbox: vi.fn(),
  listMessages: vi.fn(),
  listPermissions: vi.fn(),
  prompt: vi.fn(),
  updateInboxDelivery: vi.fn(),
  cancelInbox: vi.fn(),
}))

vi.mock('@/v2/eventStream', () => ({
  getV2EventStream: () => ({
    subscribe: (handler: (event: V2Event) => void) => {
      handlers.push(handler)
      return () => {
        const index = handlers.indexOf(handler)
        if (index >= 0) handlers.splice(index, 1)
      }
    },
    onConnected: () => () => {},
    start: () => {},
    stop: () => {},
  }),
}))

import * as api from '@/api/v2'
import { useV2SessionStore } from '@/v2/sessionStore'

const inboxItem: InboxInfo = {
  id: 'msg-race',
  session_id: 's-race',
  item: {
    type: 'user',
    payload: {
      text: 'first message',
      files: [],
      agents: [],
      skills: [],
      metadata: {},
    },
    delivery: 'steer',
  },
  status: 'pending',
  admitted_seq: 1,
  promoted_seq: null,
  time_created: 1,
  time_delivered: null,
}

const deliveredEvent = {
  id: 'evt-delivered-before-response',
  created: 2,
  type: 'session.inbox.delivered',
  data: {
    session_id: 's-race',
    inbox_id: 'msg-race',
  },
} as V2Event

const enqueuedEvent = {
  id: 'evt-enqueued-after-response',
  created: 3,
  type: 'session.inbox.enqueued',
  data: {
    session_id: 's-race',
    inbox_id: 'msg-race',
    item: inboxItem.item,
  },
} as V2Event

describe('v2 inbox pending race', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    handlers.length = 0
    vi.clearAllMocks()
  })

  it('does not resurrect the pending card when delivered arrives before prompt response', async () => {
    const store = useV2SessionStore()
    store.ensureRuntime('s-race').hydrated = true
    store.start()

    vi.mocked(api.prompt).mockImplementation(async () => {
      for (const handler of handlers) handler(deliveredEvent)
      return inboxItem
    })

    const body: PromptInput = { text: 'first message', resume: true }
    await store.send('s-race', body)

    expect(store.getRuntime('s-race')?.inbox).toEqual([])
  })

  it('still shows the pending card when enqueued arrives after prompt response', async () => {
    const store = useV2SessionStore()
    store.ensureRuntime('s-race').hydrated = true
    store.start()

    vi.mocked(api.prompt).mockResolvedValue(inboxItem)

    const body: PromptInput = { text: 'first message', resume: true }
    await store.send('s-race', body)
    for (const handler of handlers) handler(enqueuedEvent)

    expect(store.getRuntime('s-race')?.inbox.map((item) => item.id)).toEqual(['msg-race'])
  })
})
