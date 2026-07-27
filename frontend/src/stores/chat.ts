import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { FileContent, Message, AgentStep, SSEEvent, PermissionRequest } from '../types'
import {
  sendMessageStream,
  deleteConversation as apiDeleteConversation,
  deleteMessage as apiDeleteMessage,
  listConversations,
  getConversation,
  renameConversation as apiRenameConversation,
  type ConversationMeta,
} from '../api/chat'
import {
  saveSessionToCache,
  loadSessionFromCache,
  deleteSessionFromCache,
  mergeServerAndCache,
} from '../api/session-cache'
import { usePermissionStore } from './permission'

export const SUPPORTED_MODELS = [
  { value: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  { value: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'openai/gpt-4o', label: 'OpenAI GPT-4o' },
  { value: 'openai/gpt-4o-mini', label: 'OpenAI GPT-4o-mini' },
] as const

// 生成唯一 ID（优先使用 crypto.randomUUID）
function genId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
    })
  }
}

// 会话状态接口
interface SessionState {
  messages: Message[]
  conversationId: string | undefined
  conversationTitle: string
  currentSteps: AgentStep[]
  loading: boolean
  abortController: AbortController | null
  streamPhase: 'idle' | 'queued' | 'running'  // 流式阶段
  queuePosition: number | null                 // 排队位置
}

export const useChatStore = defineStore('chat', () => {
  // 所有会话的映射（sessionId -> SessionState）
  const sessions = ref<Record<string, SessionState>>({})
  // 当前活跃会话 ID
  const activeSessionId = ref<string | undefined>(undefined)
  // 会话列表
  const conversations = ref<ConversationMeta[]>([])
  // 当前选中的模型
  const selectedModel = ref(SUPPORTED_MODELS[0].value)
  // 是否启用向量数据库检索
  const useVectorDb = ref(true)

  // 获取或创建指定 ID 的会话
  function getOrCreateSession(sessionId: string, title?: string): SessionState {
    if (!sessions.value[sessionId]) {
      sessions.value[sessionId] = {
        messages: [],
        conversationId: undefined,
        conversationTitle: title || '',
        currentSteps: [],
        loading: false,
        abortController: null,
        streamPhase: 'idle',
        queuePosition: null,
      }
    }
    return sessions.value[sessionId]
  }

  // 将会话消息持久化到 IndexedDB
  async function persistSession(sessionId: string) {
    const session = sessions.value[sessionId]
    if (!session) return
    await saveSessionToCache(
      sessionId,
      session.messages,
      session.conversationId,
      session.conversationTitle,
    )
  }

  // 当前活跃会话的响应式引用
  const currentSession = computed(() => {
    if (!activeSessionId.value) return null
    return sessions.value[activeSessionId.value] || null
  })

  // 当前会话的消息列表
  const messages = computed(() => currentSession.value?.messages || [])
  // 当前会话的 ID
  const conversationId = computed(() => currentSession.value?.conversationId)
  // 当前会话的标题
  const conversationTitle = computed(() => currentSession.value?.conversationTitle)
  // 当前会话的加载状态
  const loading = computed(() => currentSession.value?.loading || false)
  // 当前会话的 Agent 执行步骤
  const currentSteps = computed(() => currentSession.value?.currentSteps || [])
  // 当前会话的流式阶段
  const streamPhase = computed(() => currentSession.value?.streamPhase || 'idle')
  // 当前会话的排队位置
  const queuePosition = computed(() => currentSession.value?.queuePosition)

  // 加载会话列表
  async function loadConversations() {
    try {
      conversations.value = await listConversations()
    } catch (e) {
      console.error('Failed to load conversations:', e)
    }
  }

  /**
   * 加载指定会话的消息 — 始终从服务器同步，与本地缓存合并
   *
   * 修复会话切换丢失问题：
   * 1. 如果会话正在 streaming（有 abortController），不覆盖本地消息，直接切过去
   * 2. 否则始终从服务器获取最新数据
   * 3. 与 IndexedDB 缓存合并，解决 SSE 中断时 assistant 内容为空的问题
   */
  async function loadConversation(id: string) {
    const meta = conversations.value.find(c => c.id === id)
    const session = getOrCreateSession(id, meta?.title)

    session.conversationId = id
    if (meta) {
      session.conversationTitle = meta.title
    }

    // 如果会话正在 streaming，保留本地消息，不覆盖
    if (session.loading || session.streamPhase !== 'idle') {
      activeSessionId.value = id
      return
    }

    // 始终从服务器获取最新数据
    try {
      const detail = await getConversation(id)
      session.conversationTitle = detail.title

      const serverMessages = detail.messages
        .filter(m => !(m.role === 'assistant' && (!m.content || m.content.trim() === '')))
        .map(m => ({
          id: m.id,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          sources: m.sources,
          steps: m.steps,
          timestamp: new Date(),
        }))

      // 从 IndexedDB 加载本地缓存（可能有 SSE 中断时的不完整数据）
      const cached = await loadSessionFromCache(id)
      const cachedMessages = cached?.messages || []

      // 合并：服务器数据为基准，本地缓存补入服务器没有的消息或更完整的 assistant 内容
      session.messages = mergeServerAndCache(serverMessages, cachedMessages)

      // 用合并后的数据更新 IndexedDB 缓存
      await saveSessionToCache(id, session.messages, id, detail.title)

      activeSessionId.value = id
    } catch (e) {
      console.error('Failed to load conversation:', e)
      // 服务器获取失败，尝试从 IndexedDB 恢复
      const cached = await loadSessionFromCache(id)
      if (cached) {
        session.messages = cached.messages
        session.conversationTitle = cached.conversationTitle || session.conversationTitle
      }
      activeSessionId.value = id
    }
  }

  // 重命名会话
  async function renameConversation(id: string, title: string) {
    try {
      await apiRenameConversation(id, title)
      const session = sessions.value[id]
      if (session) {
        session.conversationTitle = title
      }
      const idx = conversations.value.findIndex(c => c.id === id)
      if (idx >= 0) {
        conversations.value[idx].title = title
      }
    } catch (e) {
      console.error('Failed to rename conversation:', e)
    }
  }

  // 创建新会话
  function newChat() {
    activeSessionId.value = undefined
  }

  // 发送消息（支持 SSE 流式响应）
  async function send(text: string, files: FileContent[] = []) {
    let sessionId = activeSessionId.value
    if (!sessionId) {
      sessionId = genId()
      getOrCreateSession(sessionId)
      activeSessionId.value = sessionId
    }
    
    const session = sessions.value[sessionId]
    if (!session) return

    const userMsg: Message = {
      id: genId(),
      role: 'user',
      content: text,
      files: files.map(f => ({ filename: f.filename, mime_type: f.mime_type })),
      timestamp: new Date(),
    }
    session.messages = [...session.messages, userMsg]
    session.loading = true
    session.currentSteps = []

    // 发送消息后立即持久化到 IndexedDB（防止 SSE 中断丢失 user 消息）
    persistSession(sessionId)

    const reqData = {
      message: text,
      conversation_id: session.conversationId,
      model: selectedModel.value,
      use_vector_db: useVectorDb.value,
      files: files.length > 0 ? files : undefined,
    }

    const controller = new AbortController()
    session.abortController = controller
    session.streamPhase = 'queued'

    const signal = controller.signal

    try {
      await sendMessageStream(reqData, (event: SSEEvent) => {
        if (signal.aborted) return

        // 排队事件：更新排队位置，等待实际执行
        if (event.type === 'queued') {
          session.queuePosition = event.queue_position ?? null
          return
        }

        // 收到任何执行事件 → 进入运行阶段
        if (session.streamPhase !== 'running') {
          session.streamPhase = 'running'
          session.queuePosition = null
        }

        if (event.type === 'step_start' || event.type === 'step_end' ||
            event.type === 'tool_start' || event.type === 'tool_end') {
          const idx = session.currentSteps.findIndex(s => s.step_id === event.step_id)
          const step: AgentStep = {
            type: event.type as AgentStep['type'],
            step_id: event.step_id!,
            name: event.name!,
            status: event.status as AgentStep['status'],
            detail: event.detail,
            duration_ms: event.duration_ms,
            tool_name: event.tool_name,
            tool_args: event.tool_args as Record<string, unknown> | undefined,
            tool_result: event.tool_result,
          }
          if (idx >= 0) {
            session.currentSteps[idx] = step
          } else {
            session.currentSteps = [...session.currentSteps, step]
          }
        } else if (event.type === 'tool_output') {
          const idx = session.currentSteps.findIndex(
            s => s.tool_name === 'tool_execute' && s.status === 'running'
          )
          if (idx >= 0) {
            const step = session.currentSteps[idx]
            const prefix = event.source === 'stderr' ? '[stderr] ' : ''
            const line = prefix + (event.line || '')
            step.tool_output = (step.tool_output || '') + line + '\n'
          }
        } else if (event.type === 'tool_heartbeat') {
          const idx = session.currentSteps.findIndex(
            s => s.tool_name === 'tool_execute' && s.status === 'running'
          )
          if (idx >= 0) {
            const step = session.currentSteps[idx]
            step.detail = `运行中 (${event.elapsed_seconds}s)`
          }
        } else if (event.type === 'permission_request') {
          const permStore = usePermissionStore()
          permStore.handleIncoming({
            id: event.request_id!,
            path: event.path!,
            operation: event.operation!,
            tool_name: event.tool_name!,
            tool_args: event.tool_args as Record<string, unknown>,
            created_at: new Date().toISOString(),
          })
        } else if (event.type === 'done') {
          session.conversationId = event.conversation_id
          session.streamPhase = 'idle'
          session.queuePosition = null
          if (event.title) {
            session.conversationTitle = event.title
            loadConversations()
          }
          const assistantMsg: Message = {
            id: event.assistant_msg_id || genId(),
            role: 'assistant',
            content: event.answer || '',
            sources: event.sources,
            steps: event.steps || session.currentSteps,
            timestamp: new Date(),
          }
          session.messages = [...session.messages, assistantMsg]
          session.currentSteps = []
          if (event.user_msg_id) {
            for (let i = session.messages.length - 1; i >= 0; i--) {
              const m = session.messages[i]
              if (m.role === 'user' && m.id !== event.user_msg_id) {
                m.id = event.user_msg_id
                break
              }
            }
          }
          // 完成后持久化到 IndexedDB
          persistSession(sessionId)
        } else if (event.type === 'error') {
          session.streamPhase = 'idle'
          session.queuePosition = null
          session.messages = [...session.messages, {
            id: genId(),
            role: 'assistant',
            content: `Error: ${event.error || event.detail || 'Unknown error'}`,
            timestamp: new Date(),
          }]
          session.currentSteps = []
          // 出错也持久化，保留 error 消息
          persistSession(sessionId)
        }
      }, signal)
    } catch (err: any) {
      session.streamPhase = 'idle'
      session.queuePosition = null
      if (!signal.aborted && err.name !== 'AbortError') {
        session.messages = [...session.messages, {
          id: genId(),
          role: 'assistant',
          content: `Error: ${err.message}`,
          timestamp: new Date(),
        }]
        session.currentSteps = []
        // 网络错误也持久化
        persistSession(sessionId)
      }
    } finally {
      session.loading = false
      session.streamPhase = 'idle'
      session.queuePosition = null
      if (session.abortController === controller) {
        session.abortController = null
      }
    }
  }

  // 取消当前正在发送的消息
  function cancel() {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session) {
        session.abortController?.abort()
      }
    }
  }

  // 清空当前会话的消息
  function clear() {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session) {
        session.messages = []
        session.conversationId = undefined
        session.conversationTitle = ''
        session.currentSteps = []
        // 清空后删除 IndexedDB 缓存
        deleteSessionFromCache(activeSessionId.value)
      }
    }
  }

  // 撤回到指定索引处的消息
  function undoMessage(index: number) {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session) {
        session.messages = session.messages.slice(0, index)
        persistSession(activeSessionId.value)
      }
    }
  }

  // 删除当前会话
  async function deleteConversation() {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session?.conversationId) {
        try {
          await apiDeleteConversation(session.conversationId)
        } catch (e) {
          console.error('Failed to delete conversation from server:', e)
        }
      }
      // 删除 IndexedDB 缓存
      deleteSessionFromCache(activeSessionId.value)
      delete sessions.value[activeSessionId.value]
      activeSessionId.value = undefined
      loadConversations()
    }
  }

  // 删除指定消息
  async function deleteMessage(messageId: string) {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session?.conversationId) {
        try {
          await apiDeleteMessage(session.conversationId, messageId)
        } catch (e) {
          console.error('Failed to delete message from server:', e)
        }
      }
      if (session) {
        const idx = session.messages.findIndex(m => m.id === messageId)
        if (idx >= 0) {
          session.messages = session.messages.filter(m => m.id !== messageId)
          persistSession(activeSessionId.value)
        }
      }
    }
  }

  return {
    sessions, activeSessionId, conversations, selectedModel, useVectorDb,
    messages, conversationId, conversationTitle, loading, currentSteps,
    streamPhase, queuePosition,
    send, cancel, clear, undoMessage, deleteConversation, deleteMessage,
    loadConversations, loadConversation, newChat, renameConversation,
  }
})
