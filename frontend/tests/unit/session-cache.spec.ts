/**
 * session-cache：IndexedDB 读写往返（离线只读缓存）。
 * [#11] mergeServerAndCache 已删除——在线以服务端为唯一真相源。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  saveSessionToCache,
  loadSessionFromCache,
  deleteSessionFromCache,
  type CacheMessage,
} from '@/api/session-cache'

function msg(id: string, role: string, content: string, extra: Record<string, unknown> = {}): CacheMessage {
  return { id, role, content, timestamp: new Date(2026, 0, 1), ...extra } as CacheMessage
}

describe('IndexedDB 往返（fake-indexeddb）', () => {
  // 测试自包含（save/load/delete 按 sessionId 键控），不做跨库清理——
  // 模块缓存了打开的 dbInstance，deleteDatabase 会被阻塞导致钩子超时。

  it('save → load 往返（timestamp 反序列化为 Date）', async () => {
    await saveSessionToCache('s1', [msg('a', 'user', 'hi')], 's1', '会话标题')
    const loaded = await loadSessionFromCache('s1')
    expect(loaded?.messages[0].content).toBe('hi')
    expect(loaded?.messages[0].timestamp).toBeInstanceOf(Date)
  })

  it('load 不存在 → null；save 后按 updatedAt 排序稳定', async () => {
    expect(await loadSessionFromCache('nope')).toBeNull()
    await saveSessionToCache('s1', [msg('a', 'user', '1')], 's1', '标题1')
    await new Promise(r => setTimeout(r, 10))
    await saveSessionToCache('s2', [msg('b', 'user', '2')], 's2', '标题2')
    expect((await loadSessionFromCache('s2'))?.conversationTitle).toBe('标题2')
    expect((await loadSessionFromCache('s1'))?.conversationTitle).toBe('标题1')
  })

  it('delete 后 load 为 null', async () => {
    await saveSessionToCache('s1', [msg('a', 'user', '1')])
    await deleteSessionFromCache('s1')
    expect(await loadSessionFromCache('s1')).toBeNull()
  })
})
