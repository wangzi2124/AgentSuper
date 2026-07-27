/**
 * IndexedDB 会话缓存层
 *
 * 解决会话切换/SSE 中断/页面刷新时消息丢失的问题：
 * - SSE 流式接收时增量写入 IndexedDB
 * - 页面加载时从 IndexedDB 恢复消息
 * - 与服务器数据合并，取最新版本
 */

import type { Message } from '../types'

const DB_NAME = 'kb-chat-sessions'
const DB_VERSION = 1
const STORE_NAME = 'sessions'

interface CachedSession {
  sessionId: string
  messages: Message[]
  conversationId?: string
  conversationTitle?: string
  updatedAt: number
}

let dbInstance: IDBDatabase | null = null

function openDB(): Promise<IDBDatabase> {
  if (dbInstance) return Promise.resolve(dbInstance)

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'sessionId' })
        store.createIndex('updatedAt', 'updatedAt', { unique: false })
      }
    }

    request.onsuccess = (event) => {
      dbInstance = (event.target as IDBOpenDBRequest).result
      resolve(dbInstance)
    }

    request.onerror = () => reject(request.error)
  })
}

/** 保存会话消息到 IndexedDB */
export async function saveSessionToCache(
  sessionId: string,
  messages: Message[],
  conversationId?: string,
  conversationTitle?: string,
): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      const data: CachedSession = {
        sessionId,
        // 序列化时将 Date 转为 ISO 字符串，反序列化时恢复
        messages: messages.map(m => ({
          ...m,
          timestamp: m.timestamp instanceof Date ? m.timestamp : new Date(m.timestamp),
        })),
        conversationId,
        conversationTitle,
        updatedAt: Date.now(),
      }
      const req = store.put(data)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch (e) {
    console.warn('Failed to save session to IndexedDB:', e)
  }
}

/** 从 IndexedDB 加载会话消息 */
export async function loadSessionFromCache(sessionId: string): Promise<CachedSession | null> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const store = tx.objectStore(STORE_NAME)
      const req = store.get(sessionId)
      req.onsuccess = () => {
        const result = req.result as CachedSession | undefined
        if (result) {
          // 反序列化：将 ISO 字符串恢复为 Date 对象
          result.messages = result.messages.map(m => ({
            ...m,
            timestamp: typeof m.timestamp === 'string' ? new Date(m.timestamp) : m.timestamp,
          }))
        }
        resolve(result || null)
      }
      req.onerror = () => reject(req.error)
    })
  } catch (e) {
    console.warn('Failed to load session from IndexedDB:', e)
    return null
  }
}

/** 从 IndexedDB 加载所有会话的 sessionId 列表（按 updatedAt 倒序） */
export async function loadAllSessionIds(): Promise<string[]> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const store = tx.objectStore(STORE_NAME)
      const req = store.getAll()
      req.onsuccess = () => {
        const results = req.result as CachedSession[]
        results.sort((a, b) => b.updatedAt - a.updatedAt)
        resolve(results.map(r => r.sessionId))
      }
      req.onerror = () => reject(req.error)
    })
  } catch (e) {
    console.warn('Failed to load sessions from IndexedDB:', e)
    return []
  }
}

/** 删除 IndexedDB 中的会话缓存 */
export async function deleteSessionFromCache(sessionId: string): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      const req = store.delete(sessionId)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch (e) {
    console.warn('Failed to delete session from IndexedDB:', e)
  }
}

/**
 * 合并服务器数据与本地缓存
 *
 * 策略：
 * - 以服务器数据为基准（服务器是 source of truth）
 * - 本地缓存中如果有服务器没有的消息（比如 SSE 中断时的 user 消息），补入
 * - 如果同一 ID 的消息两端都有，取 content 更长的版本（服务器的 assistant 回答通常更完整）
 */
export function mergeServerAndCache(
  serverMessages: Message[],
  cachedMessages: Message[],
): Message[] {
  if (cachedMessages.length === 0) return serverMessages
  if (serverMessages.length === 0) return cachedMessages

  const merged = new Map<string, Message>()

  // 先放服务器数据
  for (const msg of serverMessages) {
    merged.set(msg.id, msg)
  }

  // 合并本地缓存
  for (const msg of cachedMessages) {
    const existing = merged.get(msg.id)
    if (!existing) {
      // 服务器没有这条消息，补入（可能是 SSE 中断时的 user 消息）
      merged.set(msg.id, msg)
    } else if (msg.role === 'assistant') {
      // assistant 消息：优先取有 sources/steps 的版本，其次取 content 更长的
      const existingHasMeta = !!(existing as any).sources || !!(existing as any).steps
      const cachedHasMeta = !!(msg as any).sources || !!(msg as any).steps
      if (cachedHasMeta && !existingHasMeta) {
        merged.set(msg.id, msg)
      } else if (msg.content.length > existing.content.length) {
        merged.set(msg.id, msg)
      }
    }
  }

  // 按原始顺序排列（服务器顺序为准，本地特有的追加到末尾）
  const result: Message[] = []
  const serverIds = new Set(serverMessages.map(m => m.id))

  for (const msg of serverMessages) {
    result.push(merged.get(msg.id) || msg)
  }

  for (const msg of cachedMessages) {
    if (!serverIds.has(msg.id)) {
      result.push(msg)
    }
  }

  return result
}
