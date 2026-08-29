/**
 * session-cache：mergeServerAndCache 合并策略 + IndexedDB 读写往返。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  saveSessionToCache,
  loadSessionFromCache,
  deleteSessionFromCache,
  loadAllSessionIds,
  mergeServerAndCache,
  type CacheMessage,
} from '@/api/session-cache'

function msg(id: string, role: string, content: string, extra: Record<string, unknown> = {}): CacheMessage {
  return { id, role, content, timestamp: new Date(2026, 0, 1), ...extra } as CacheMessage
}

describe('mergeServerAndCache', () => {
  it('无缓存 → 返回服务器数据', () => {
    const s = [msg('a', 'user', 'hi')]
    expect(mergeServerAndCache(s, [])).toEqual(s)
  })

  it('无服务器 → 返回缓存（过滤墓碑/live）', () => {
    const c = [msg('a', 'user', 'hi'), msg('live', 'assistant', 'x', { live: true })]
    expect(mergeServerAndCache([], c)).toEqual([c[0]])
  })

  it('墓碑过滤：deletedIds 中的消息两端都不复活', () => {
    const s = [msg('a', 'user', 'hi'), msg('b', 'user', 'gone')]
    const c = [msg('b', 'user', 'gone')]
    const out = mergeServerAndCache(s, c, ['b'])
    expect(out.map(m => m.id)).toEqual(['a'])
  })

  it('缓存独有消息（SSE 中断的 user 消息）补入末尾', () => {
    const s = [msg('a', 'assistant', 'A')]
    const c = [msg('a', 'assistant', 'A'), msg('local', 'user', '未同步')]
    const out = mergeServerAndCache(s, c)
    expect(out.map(m => m.id)).toEqual(['a', 'local'])
  })

  it('assistant 同 id：缓存带元数据版本胜出（sources/steps/parts/agents）', () => {
    const s = [msg('a', 'assistant', 'server 短')]
    const c = [msg('a', 'assistant', 'server 短', { agents: [{ agent_id: 'rag' }] })]
    const out = mergeServerAndCache(s, c)
    expect((out[0] as any).agents).toBeTruthy()
  })

  it('assistant 同 id：content 更长者胜出（服务器完整回答通常更长）', () => {
    const s = [msg('a', 'assistant', '短回答')]
    const c = [msg('a', 'assistant', '这是一条更长的缓存回答内容')]
    expect(mergeServerAndCache(s, c)[0].content).toBe('这是一条更长的缓存回答内容')
    // 服务器更长 → 服务器胜出
    const s2 = [msg('a', 'assistant', '服务器极长回答' + 'x'.repeat(50))]
    expect(mergeServerAndCache(s2, c)[0].content).toBe(s2[0].content)
  })

  it('user 同 id：服务器优先（不覆盖）', () => {
    const s = [msg('a', 'user', 'server')]
    const c = [msg('a', 'user', 'cache 更长但 user 不参与替换')]
    expect(mergeServerAndCache(s, c)[0].content).toBe('server')
  })

  it('顺序：服务器顺序为主，本地独有追加末尾', () => {
    const s = [msg('b', 'assistant', 'B'), msg('c', 'assistant', 'C')]
    const c = [msg('a', 'user', 'A'), msg('b', 'assistant', 'B')]
    expect(mergeServerAndCache(s, c).map(m => m.id)).toEqual(['b', 'c', 'a'])
  })
})

describe('IndexedDB 往返（fake-indexeddb）', () => {
  // 测试自包含（save/load/delete 按 sessionId 键控），不做跨库清理——
  // 模块缓存了打开的 dbInstance，deleteDatabase 会被阻塞导致钩子超时。

  it('save → load 往返（timestamp 反序列化为 Date）', async () => {
    await saveSessionToCache('s1', [msg('a', 'user', 'hi')], 's1', '会话标题', ['del1'])
    const loaded = await loadSessionFromCache('s1')
    expect(loaded?.messages[0].content).toBe('hi')
    expect(loaded?.messages[0].timestamp).toBeInstanceOf(Date)
    expect(loaded?.deletedIds).toEqual(['del1'])
  })

  it('load 不存在 → null；loadAll 按 updatedAt 倒序', async () => {
    expect(await loadSessionFromCache('nope')).toBeNull()
    await saveSessionToCache('s1', [msg('a', 'user', '1')])
    await new Promise(r => setTimeout(r, 10))
    await saveSessionToCache('s2', [msg('b', 'user', '2')])
    const ids = await loadAllSessionIds()
    expect(ids[0]).toBe('s2') // 后写入 updatedAt 更大
  })

  it('delete 后 load 为 null', async () => {
    await saveSessionToCache('s1', [msg('a', 'user', '1')])
    await deleteSessionFromCache('s1')
    expect(await loadSessionFromCache('s1')).toBeNull()
  })
})
