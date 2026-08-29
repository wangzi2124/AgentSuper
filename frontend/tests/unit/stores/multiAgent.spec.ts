/**
 * multiAgent store：发送流（SSE 驱动）、错误分类、撤销/删除墓碑、会话迁移、重试。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import type { MultiAgentSSEEvent } from '@/types'

const mocks = vi.hoisted(() => ({
  sendStream: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  revertSession: vi.fn(),
  deleteSessionMessage: vi.fn(),
  deleteConversation: vi.fn(),
  saveCache: vi.fn(),
  loadCache: vi.fn(),
  deleteCache: vi.fn(),
  mergeServerAndCache: (s: any[], c: any[], d?: string[]) =>
    [...(s || []), ...(c || [])].filter(m => !m?.live && !(d || []).includes(m?.id)),
}))

vi.mock('@/api/multiAgent', () => ({ sendMultiAgentStream: mocks.sendStream }))
vi.mock('@/api/sessions', () => ({
  listConversations: mocks.listConversations,
  getConversation: mocks.getConversation,
  renameConversation: vi.fn(),
  deleteConversation: mocks.deleteConversation,
  interruptSession: vi.fn(),
  revertSession: mocks.revertSession,
  deleteSessionMessage: mocks.deleteSessionMessage,
}))
vi.mock('@/api/session-cache', () => ({
  saveSessionToCache: mocks.saveCache,
  loadSessionFromCache: mocks.loadCache,
  deleteSessionFromCache: mocks.deleteCache,
  mergeServerAndCache: mocks.mergeServerAndCache,
}))
vi.mock('@/api/errors', () => ({
  classifyNetworkError: (e: any) => ({ type: 'network', message: String(e), retryable: true }),
}))

import { useMultiAgentStore } from '@/stores/multiAgent'

let uid = 0
let rngSpy: any
beforeEach(() => {
  setActivePinia(createPinia())
  uid = 0
  mocks.sendStream.mockReset()
  mocks.getConversation.mockReset()
  mocks.revertSession.mockReset()
  mocks.deleteSessionMessage.mockReset()
  mocks.saveCache.mockResolvedValue(undefined)
  mocks.loadCache.mockResolvedValue(null)
  mocks.deleteCache.mockResolvedValue(undefined)
  rngSpy = vi.spyOn(crypto, 'randomUUID').mockImplementation(() => `id-${uid++}`)
})
afterEach(() => {
  rngSpy?.mockRestore()
})

function ev(partial: Partial<MultiAgentSSEEvent>): MultiAgentSSEEvent {
  return partial as MultiAgentSSEEvent
}

const lastMsg = (store: any) => store.messages[store.messages.length - 1]

describe('send 流式', () => {
  it('queued → text_delta → done 全链路', async () => {
    mocks.sendStream.mockImplementation(async (_req: unknown, onEvent: (e: MultiAgentSSEEvent) => void, _signal?: unknown, onSid?: (s: string) => void) => {
      onSid?.('server-1')
      onEvent(ev({ type: 'queued', queue_position: 2 }))
      onEvent(ev({ type: 'text_delta', delta: '你' }))
      onEvent(ev({ type: 'text_delta', delta: '好' }))
      onEvent(ev({ type: 'done', conversation_id: 'server-1', answer: '最终答案', assistant_msg_id: 'am1', user_msg_id: 'um1' }))
    })
    const store = useMultiAgentStore()
    const ok = await store.send('hello')
    expect(ok).toBe(true)
    expect(store.messages.length).toBe(2)
    expect(store.messages[0].role).toBe('user')
    expect(store.messages[1].content).toBe('最终答案')
    expect(store.conversationId).toBe('server-1')
    expect(store.streamPhase).toBe('idle')
    // done 后内存 key 迁移到服务器 id
    expect(store.activeSessionId).toBe('server-1')
    // 首事件即拿到会话 id（中断可打断）
    expect(mocks.sendStream.mock.calls[0][2]).toBeTruthy()
  })

  it('模型不可用 → 拒绝发送 + notice', async () => {
    const store = useMultiAgentStore()
    store.selectedModel = 'bogus-model'
    expect(await store.send('x')).toBe(false)
    expect(store.notice).toContain('模型不可用')
    expect(mocks.sendStream).not.toHaveBeenCalled()
  })

  it('loading 中并发发送 → 拒绝 + 排队提示', async () => {
    let release!: () => void
    mocks.sendStream.mockImplementation(() => new Promise<void>(r => { release = r }))
    const store = useMultiAgentStore()
    const p1 = store.send('first')
    expect(store.loading).toBe(true)
    expect(await store.send('second')).toBe(false)
    expect(store.notice).toContain('处理中')
    release()
    await p1
  })
})

describe('错误分类与自动重试', () => {
  it('429 可重试 → rate_limit + 自动重试倒计时', async () => {
    mocks.sendStream.mockImplementation(async (_req: unknown, onEvent: (e: MultiAgentSSEEvent) => void) => {
      onEvent(ev({ type: 'error', error: 'rate', retryable: true, status_code: 429, error_type: 'QueueFullError' }))
    })
    const store = useMultiAgentStore()
    await store.send('hello')
    const err = lastMsg(store)
    expect(err.isError).toBe(true)
    expect(err.errorInfo?.type).toBe('rate_limit')
    expect(err.content).toContain('请求过于频繁')
    expect(store.retryCountdown).toBeGreaterThan(0)
    store.cancelAutoRetry()
  })

  it('503 → server_error；无 status 可重试 → network', async () => {
    mocks.sendStream.mockImplementation(async (_req: unknown, onEvent: (e: MultiAgentSSEEvent) => void) => {
      onEvent(ev({ type: 'error', error: 'svc', retryable: true, status_code: 503 }))
    })
    const store = useMultiAgentStore()
    await store.send('hello')
    expect(lastMsg(store).errorInfo?.type).toBe('server_error')
    expect(lastMsg(store).content).toContain('服务器错误')
    store.cancelAutoRetry()

    mocks.sendStream.mockImplementation(async (_req: unknown, onEvent: (e: MultiAgentSSEEvent) => void) => {
      onEvent(ev({ type: 'error', error: 'net', retryable: true }))
    })
    await store.send('again')
    expect(lastMsg(store).errorInfo?.type).toBe('network')
    expect(lastMsg(store).content).toContain('网络连接中断')
    store.cancelAutoRetry()
  })

  it('非可重试错误 → unknown + 不启动自动重试', async () => {
    mocks.sendStream.mockImplementation(async (_req: unknown, onEvent: (e: MultiAgentSSEEvent) => void) => {
      onEvent(ev({ type: 'error', error: 'bad request', retryable: false }))
    })
    const store = useMultiAgentStore()
    await store.send('hello')
    expect(lastMsg(store).errorInfo?.type).toBe('unknown')
    expect(store.retryCountdown).toBe(0)
  })
})

describe('撤销 / 删除（墓碑）', () => {
  it('undoMessage 切片 + 墓碑 + 同步后端', async () => {
    const store = useMultiAgentStore()
    store.activeSessionId = 's1'
    store.sessions['s1'] = {
      messages: [
        { id: 'm0', role: 'user', content: 'a', agents: [], timestamp: new Date() },
        { id: 'm1', role: 'assistant', content: 'A', agents: [], timestamp: new Date() },
        { id: 'm2', role: 'user', content: 'b', agents: [], timestamp: new Date() },
        { id: 'm3', role: 'assistant', content: 'B', agents: [], timestamp: new Date() },
      ],
      conversationId: 'c1', conversationTitle: '', loading: false, abortController: null,
      streamPhase: 'idle', queuePosition: null, deletedIds: [],
    }
    await store.undoMessage(2)
    expect(store.messages.map(m => m.id)).toEqual(['m0', 'm1'])
    expect(store.sessions['s1'].deletedIds).toEqual(['m2', 'm3'])
    expect(mocks.revertSession).toHaveBeenCalledWith('c1', 'm1')
    expect(mocks.saveCache).toHaveBeenCalled()
  })

  it('deleteMessage 过滤 + 墓碑', async () => {
    const store = useMultiAgentStore()
    store.activeSessionId = 's1'
    store.sessions['s1'] = {
      messages: [
        { id: 'm0', role: 'user', content: 'a', agents: [], timestamp: new Date() },
        { id: 'm1', role: 'assistant', content: 'A', agents: [], timestamp: new Date() },
      ],
      conversationId: 'c1', conversationTitle: '', loading: false, abortController: null,
      streamPhase: 'idle', queuePosition: null, deletedIds: [],
    }
    await store.deleteMessage('m1')
    expect(store.messages.map(m => m.id)).toEqual(['m0'])
    expect(store.sessions['s1'].deletedIds).toContain('m1')
    expect(mocks.deleteSessionMessage).toHaveBeenCalledWith('c1', 'm1')
  })
})

describe('会话加载', () => {
  it('loadConversation 合并服务器 + 缓存 + 同步工作目录', async () => {
    mocks.getConversation.mockResolvedValue({
      id: 'c1', title: '标题', directory: '/work',
      messages: [{ id: 'm0', role: 'user', content: 'server', agents: [], files: [] }],
    })
    mocks.loadCache.mockResolvedValue({
      messages: [{ id: 'local', role: 'user', content: '本地未同步', agents: [], timestamp: new Date() }],
      deletedIds: [],
    })
    const store = useMultiAgentStore()
    await store.loadConversation('c1')
    expect(store.activeSessionId).toBe('c1')
    expect(store.sessionDirectory).toBe('/work')
    const ids = store.messages.map(m => m.id)
    expect(ids).toContain('m0')
    expect(ids).toContain('local')
  })
})
