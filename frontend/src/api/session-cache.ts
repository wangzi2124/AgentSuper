/**
 * IndexedDB 会话缓存层
 *
 * [#11 双真相源] 在线时服务端是唯一真相源（后端在回合开始即落库 user 消息、
 * done/断连兜底落库 assistant），IndexedDB 只做**离线只读**回退：服务器取不到时
 * 展示最后缓存的内容。不再与服务器消息合并（mergeServerAndCache 已删除）。
 *
 * - SSE 流式接收时增量写入 IndexedDB（仅作离线快照）
 * - 页面加载时先取服务器；失败则回退 IndexedDB
 *
 * 消息类型泛型化：同时服务单 Agent（Message）与多 Agent（MultiAgentMessage）。
 */

const DB_NAME = 'kb-chat-sessions'
const DB_VERSION = 1
const STORE_NAME = 'sessions'

// 缓存所需的消息最小形状（多 Agent 消息多出的 agents/isError 等字段原样透传）
export interface CacheMessage {
  id: string
  role: string
  content: string
  timestamp: Date
  live?: boolean
}

interface CachedSession<M extends CacheMessage> {
  sessionId: string
  messages: M[]
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
export async function saveSessionToCache<M extends CacheMessage>(
  sessionId: string,
  messages: M[],
  conversationId?: string,
  conversationTitle?: string,
): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      // JSON round-trip: strip non-cloneable values (functions, circular refs, undefined, Map/Set, etc.)
      // that would cause DataCloneError in IndexedDB structured clone
      const raw: CachedSession<M> = {
        sessionId,
        messages: messages.map(m => ({
          ...m,
          timestamp: m.timestamp instanceof Date ? m.timestamp : new Date(m.timestamp),
        })),
        conversationId,
        conversationTitle,
        updatedAt: Date.now(),
      }
      const data: CachedSession<M> = JSON.parse(JSON.stringify(raw))
      const req = store.put(data)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch (e) {
    console.warn('Failed to save session to IndexedDB:', e)
  }
}

/** 从 IndexedDB 加载会话消息 */
export async function loadSessionFromCache<M extends CacheMessage>(sessionId: string): Promise<CachedSession<M> | null> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const store = tx.objectStore(STORE_NAME)
      const req = store.get(sessionId)
      req.onsuccess = () => {
        const result = req.result as CachedSession<M> | undefined
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

// [D2] `loadAllSessionIds()` 已删除：会话列表以服务端 `/api/sessions` 为准（store
// `loadConversations`），IndexedDB 只做单会话离线只读缓存，列全量 id 无人调用。

// [#11] `mergeServerAndCache()` 已删除：后端回合开始即落库 user 消息、done/断连兜底
// 落库 assistant，在线刷新以服务端为准；缓存仅作离线回退，不再参与在线合并。

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
