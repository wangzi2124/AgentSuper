import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { FileContent, Message, AgentStep, SSEEvent, ChatError, PermissionRequest } from '../types'
import {
  sendMessageStream,
} from '../api/chat'
import {
  listConversations,
  getConversation,
  renameConversation as apiRenameConversation,
  deleteConversation as apiDeleteConversation,
  type ConversationMeta,
} from '../api/sessions'
import {
  saveSessionToCache,
  loadSessionFromCache,
  deleteSessionFromCache,
  mergeServerAndCache,
} from '../api/session-cache'
import { usePermissionStore } from './permission'
import { interruptSession, revertSession, deleteSessionMessage } from '../api/sessions'

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
  liveMsgId: string | null                     // 流式中的占位 assistant 消息 id（text_delta 累积）
  liveContent: string                          // 流式中的文本增量累积
  deletedIds: string[]                         // 已删除消息 id（墓碑，防 merge 复活）
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
  const useVectorDb = ref(false)
  // 当前/新建会话绑定的工作目录（opencode ctx.directory）。首条消息发送时
  // 随 ChatRequest.directory 创建会话；已有会话在 loadConversation 时同步为服务器值。
  const sessionDirectory = ref('')

  // 设置当前会话的工作目录（新建对话时选择）
  function setSessionDirectory(dir: string) {
    sessionDirectory.value = dir || ''
  }

  // --- 重试机制 ---
  const AUTO_RETRY_DELAY = 5 // 秒
  const MAX_AUTO_RETRIES = 2
  let autoRetryTimer: ReturnType<typeof setTimeout> | null = null
  const retryCountdown = ref(0)
  const autoRetrySessionId = ref<string | undefined>(undefined)
  const retryMessageText = ref('')

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
        liveMsgId: null,
        liveContent: '',
        deletedIds: [],
      }
    }
    return sessions.value[sessionId]
  }

  // 将会话消息持久化到 IndexedDB
  async function persistSession(sessionId: string) {
    const session = sessions.value[sessionId]
    if (!session) return
    // 持久化前过滤 live 占位消息，防止中断/取消后残留占位泄漏进缓存
    const messages = session.messages.filter(m => !m.live)
    // P0.1: 服务器已分配会话 id 后，缓存 key 一律以服务器 id 为准，
    // 否则新会话（客户端 genId）写完缓存后再按服务器 id 读会永远 miss
    const cacheKey = session.conversationId || sessionId
    await saveSessionToCache(
      cacheKey,
      messages,
      session.conversationId,
      session.conversationTitle,
      session.deletedIds,
    )
    // 清理 genId 孤儿条目（客户端临时 key → 服务器 id 迁移）
    if (cacheKey !== sessionId) {
      await deleteSessionFromCache(sessionId)
    }
  }

  // --- 错误分类与格式化 ---

  function classifySSEError(event: SSEEvent): ChatError {
    const detail = event.detail || event.error || 'Unknown error'
    if (event.retryable) {
      if (event.status_code === 429) return { type: 'rate_limit', message: detail, retryable: true, statusCode: 429 }
      if (event.status_code === 503) return { type: 'server_error', message: detail, retryable: true, statusCode: 503 }
      if (event.status_code && event.status_code >= 500) return { type: 'server_error', message: detail, retryable: true, statusCode: event.status_code }
      return { type: 'network', message: detail, retryable: true }
    }
    return { type: 'unknown', message: detail, retryable: false }
  }

  function classifyNetworkError(err: unknown): ChatError {
    const msg = err instanceof Error ? err.message : String(err)
    const lower = msg.toLowerCase()
    if (lower.includes('abort') || lower.includes('aborted')) {
      return { type: 'unknown', message: msg, retryable: false }
    }
    if (lower.includes('timeout') || lower.includes('timed out')) {
      return { type: 'timeout', message: msg, retryable: true }
    }
    if (lower.includes('rate limit') || lower.includes('429') || lower.includes('too many requests')) {
      return { type: 'rate_limit', message: msg, retryable: true }
    }
    if (lower.includes('failed to fetch') || lower.includes('networkerror')) {
      return { type: 'network', message: msg, retryable: true }
    }
    if (lower.includes('500') || lower.includes('502') || lower.includes('503') || lower.includes('504')) {
      return { type: 'server_error', message: msg, retryable: true }
    }
    return { type: 'unknown', message: msg, retryable: false }
  }

  function formatErrorMessage(info: ChatError): string {
    switch (info.type) {
      case 'rate_limit': return `请求过于频繁（${info.statusCode || 429}），请稍后重试`
      case 'server_error': return `服务器错误（${info.statusCode || 500}），请稍后重试`
      case 'network': return `网络连接中断，请检查网络后重试`
      case 'timeout': return `请求超时，请稍后重试`
      default: return `出错了: ${info.message}`
    }
  }

  // --- 自动重试 ---

  function startAutoRetry(sessionId: string, text: string) {
    const session = sessions.value[sessionId]
    if (!session) return

    // 检查自动重试次数（通过计算连续 error 消息数）
    let consecutiveErrors = 0
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].isError) consecutiveErrors++
      else break
    }
    if (consecutiveErrors >= MAX_AUTO_RETRIES) {
      cancelAutoRetry()
      return
    }

    cancelAutoRetry() // 清除之前的计时器
    retryCountdown.value = AUTO_RETRY_DELAY
    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    autoRetryTimer = setInterval(() => {
      retryCountdown.value--
      if (retryCountdown.value <= 0) {
        cancelAutoRetry()
        // 执行重试
        retryLastMessage()
      }
    }, 1000)
  }

  function cancelAutoRetry() {
    if (autoRetryTimer) {
      clearInterval(autoRetryTimer)
      autoRetryTimer = null
    }
    retryCountdown.value = 0
    autoRetrySessionId.value = undefined
    retryMessageText.value = ''
  }

  // 重试最后一条用户消息
  async function retryLastMessage() {
    const sessionId = autoRetrySessionId.value || activeSessionId.value
    if (!sessionId) return
    const session = sessions.value[sessionId]
    if (!session) return

    // 找到最后一条 user 消息
    const lastUserMsg = [...session.messages].reverse().find(m => m.role === 'user')
    if (!lastUserMsg) return

    // 删除最后一条 error 消息（如果有）
    const lastMsg = session.messages[session.messages.length - 1]
    if (lastMsg?.isError) {
      session.messages = session.messages.slice(0, -1)
    }

    // 同时移除原 user 消息，避免重试后产生重复的用户消息
    const lastUserMsgIdx = session.messages.findIndex(m => m.id === lastUserMsg.id)
    if (lastUserMsgIdx >= 0) {
      session.messages = session.messages.slice(0, lastUserMsgIdx)
    }

    // 重新发送
    await send(lastUserMsg.content)
  }

  // 手动重试（从 UI 触发）
  async function manualRetry(messageId: string) {
    if (!activeSessionId.value) return
    const session = sessions.value[activeSessionId.value]
    if (!session) return

    cancelAutoRetry()

    // 找到这条 error 消息的前一条 user 消息
    const errorIdx = session.messages.findIndex(m => m.id === messageId)
    if (errorIdx < 0) return

    // 向前找最近的 user 消息
    let userIdx = -1
    for (let i = errorIdx - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') {
        userIdx = i
        break
      }
    }
    if (userIdx < 0) return

    const userMsg = session.messages[userIdx]

    // 删除 user 消息及其后的 error 消息，避免重试产生重复的用户消息
    session.messages = session.messages.slice(0, userIdx)

    // 重新发送
    await send(userMsg.content)
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

  const CONV_TYPE = 'chat'

  // 加载会话列表
  async function loadConversations() {
    try {
      conversations.value = await listConversations(CONV_TYPE)
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
      const detail = await getConversation(id, CONV_TYPE)
      session.conversationTitle = detail.title
      // 同步会话绑定目录（服务器为准）
      if (detail.directory) {
        sessionDirectory.value = detail.directory
      }

      const serverMessages = detail.messages
        .filter(m => !(m.role === 'assistant' && (!m.content || m.content.trim() === '')))
        .map(m => ({
          id: m.id,
          role: m.role as Message['role'],
          content: m.content,
          sources: m.sources,
          steps: m.steps,
          parts: m.parts,
          timestamp: new Date(),
        }))

      // 从 IndexedDB 加载本地缓存（可能有 SSE 中断时的不完整数据）
      const cached = await loadSessionFromCache(id)
      const cachedMessages = cached?.messages || []
      // 继承已删除消息的墓碑列表，保证被删消息不因缓存而复活
      session.deletedIds = cached?.deletedIds || []

      // 合并：服务器数据为基准，本地缓存补入服务器没有的消息或更完整的 assistant 内容
      session.messages = mergeServerAndCache(serverMessages, cachedMessages, session.deletedIds)

      // 用合并后的数据更新 IndexedDB 缓存
      await saveSessionToCache(id, session.messages, id, detail.title, session.deletedIds)

      activeSessionId.value = id
    } catch (e) {
      console.error('Failed to load conversation:', e)
      // 服务器获取失败，尝试从 IndexedDB 恢复
      const cached = await loadSessionFromCache(id)
      if (cached) {
        // 过滤 live 占位 + 应用墓碑，避免恢复出已删/未完成消息
        session.deletedIds = cached.deletedIds || []
        const tomb = new Set(session.deletedIds)
        session.messages = cached.messages.filter(m => !m.live && !tomb.has(m.id))
        session.conversationTitle = cached.conversationTitle || session.conversationTitle
      }
      activeSessionId.value = id
    }
  }

  // 重命名会话
  async function renameConversation(id: string, title: string) {
    try {
      await apiRenameConversation(id, title, CONV_TYPE)
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
    // 防止同一会话并发发送导致消息/步骤竞态
    if (session.loading) return
    // 用户主动发送新消息时，取消待执行的自动重试
    cancelAutoRetry()

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

    // 设置自动重试的 session ID（用于错误时自动重试）
    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    // 发送消息后立即持久化到 IndexedDB（防止 SSE 中断丢失 user 消息）
    persistSession(sessionId)

    const reqData = {
      message: text,
      conversation_id: session.conversationId,
      model: selectedModel.value,
      use_vector_db: useVectorDb.value,
      files: files.length > 0 ? files : undefined,
      directory: sessionDirectory.value || undefined,
    }

    const controller = new AbortController()
    session.abortController = controller
    session.streamPhase = 'queued'

    const signal = controller.signal

    try {
      await sendMessageStream(reqData, (event: SSEEvent) => {
        if (signal.aborted) return

        // 尽早记录服务器会话 id（后端在首个事件即注入），保证新会话也能被"停止"打断
        if (event.conversation_id && !session.conversationId) {
          session.conversationId = event.conversation_id
        }

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
        } else if (event.type === 'text_delta') {
          // 流式文本增量：累积进占位 assistant 消息（对齐 opencode text-delta 事件）
          session.liveContent += event.delta || ''
          if (!session.liveMsgId) {
            session.liveMsgId = 'live-' + genId()
            session.messages = [...session.messages, {
              id: session.liveMsgId,
              role: 'assistant',
              content: session.liveContent,
              timestamp: new Date(),
              live: true,
            }]
          } else {
            session.messages = session.messages.map(m =>
              m.id === session.liveMsgId ? { ...m, content: session.liveContent } : m
            )
          }
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
            parts: event.parts,
            timestamp: new Date(),
          }
          // 流式期间有占位消息 → 用最终消息替换（保留其位置）
          if (session.liveMsgId) {
            session.messages = session.messages.map(m =>
              m.id === session.liveMsgId ? assistantMsg : m
            )
            session.liveMsgId = null
            session.liveContent = ''
          } else {
            session.messages = [...session.messages, assistantMsg]
          }
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
          // 内存 key 同步：新会话完成后把 sessions map 从客户端 genId 迁移到服务器 id，
          // 保证侧边栏（以 server id 列出）点击时命中同一 session 对象而不是重建
          const serverId = session.conversationId
          if (serverId && serverId !== sessionId) {
            sessions.value[serverId] = session
            delete sessions.value[sessionId]
            if (activeSessionId.value === sessionId) {
              activeSessionId.value = serverId
            }
            if (autoRetrySessionId.value === sessionId) {
              autoRetrySessionId.value = serverId
            }
          }
          // 成功则取消自动重试
          cancelAutoRetry()
        } else if (event.type === 'error') {
          session.streamPhase = 'idle'
          session.queuePosition = null
          // 移除流式占位消息，避免与错误消息并存
          if (session.liveMsgId) {
            session.messages = session.messages.filter(m => m.id !== session.liveMsgId)
            session.liveMsgId = null
            session.liveContent = ''
          }
          const errorInfo: ChatError = classifySSEError(event)
          session.messages = [...session.messages, {
            id: genId(),
            role: 'assistant',
            content: formatErrorMessage(errorInfo),
            timestamp: new Date(),
            isError: true,
            errorInfo,
          }]
          session.currentSteps = []
          persistSession(sessionId)
          // 自动重试：仅对可重试错误且未超过最大次数
          if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
            startAutoRetry(sessionId, text)
          }
        }
      }, signal, (sid) => {
        // 后端在响应头 X-Session-Id 立即透出会话 id（先于任何 SSE 事件），
        // 保证新会话在排队/等待全局并发槽位时，"停止/撤销"也能 POST /interrupt 打断后台任务
        if (!session.conversationId) {
          session.conversationId = sid
        }
      })
    } catch (err: any) {
      session.streamPhase = 'idle'
      session.queuePosition = null
      // 判断是否为 ChatError（从 API 层抛出的结构化错误）
      const isChatError = err && typeof err === 'object' && 'retryable' in err && 'type' in err
      const errorInfo: ChatError = isChatError
        ? err as ChatError
        : classifyNetworkError(err)
      // 除用户主动取消外，任何失败都应展示错误消息（避免静默失败）
      if (!signal.aborted) {
        if (session.liveMsgId) {
          session.messages = session.messages.filter(m => m.id !== session.liveMsgId)
          session.liveMsgId = null
          session.liveContent = ''
        }
        session.messages = [...session.messages, {
          id: genId(),
          role: 'assistant',
          content: formatErrorMessage(errorInfo),
          timestamp: new Date(),
          isError: true,
          errorInfo,
        }]
        session.currentSteps = []
        persistSession(sessionId)
        // 自动重试：仅对可重试错误
        if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
          startAutoRetry(sessionId, text)
        }
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
        // 本地中断 SSE 连接（前端立即停止接收）
        session.abortController?.abort()
        // 通知后端真正停止 Agent 任务（fire-and-forget，本地 abort 不会让后端停止）
        if (session.conversationId) {
          interruptSession(session.conversationId).catch((e) => {
            console.error('Failed to interrupt session:', e)
          })
        }
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
        session.deletedIds = []
        session.liveMsgId = null
        session.liveContent = ''
        // 清空后删除 IndexedDB 缓存
        deleteSessionFromCache(activeSessionId.value)
      }
    }
  }

  // 撤回到指定索引处的消息（删除该索引及其之后的消息）
  async function undoMessage(index: number) {
    if (!activeSessionId.value) return
    const session = sessions.value[activeSessionId.value]
    if (!session) return

    const target = session.messages[index - 1]
    const removedIds = session.messages.slice(index).map(m => m.id)

    // P0.4: 服务端撤销到目标消息（删除其后所有消息/部件），避免本地截断与服务器不一致
    if (session.conversationId && target?.id) {
      try {
        await revertSession(session.conversationId, target.id)
      } catch (e) {
        console.error('Failed to revert session on server:', e)
      }
    }

    session.messages = session.messages.slice(0, index)
    // 记录墓碑，防止 IndexedDB 缓存把已撤销消息合并复活
    if (removedIds.length) {
      session.deletedIds = [...new Set([...session.deletedIds, ...removedIds])]
    }
    await persistSession(activeSessionId.value)
  }

  // 删除当前会话（或指定 id 的会话）
  async function deleteConversation(id?: string) {
    const sid = id || activeSessionId.value
    if (!sid) return
    const session = sessions.value[sid]
    // 取消未执行的自动重试，避免删除后残留定时器再次发送
    cancelAutoRetry()
    // 中断正在进行的流式请求，防止 late callback 写回已删除会话
    session?.abortController?.abort()
    // 服务器删除：以会话绑定的 conversationId 为准（新会话可能仍为客户端 genId）
    const serverId = session?.conversationId || sid
    if (serverId) {
      try {
        await apiDeleteConversation(serverId, CONV_TYPE)
      } catch (e) {
        console.error('Failed to delete conversation from server:', e)
      }
    }
    // 删除 IndexedDB 缓存（服务器 id + 可能的 genId 临时 key）
    await deleteSessionFromCache(serverId)
    if (serverId !== sid) {
      await deleteSessionFromCache(sid)
    }
    delete sessions.value[sid]
    if (activeSessionId.value === sid) {
      activeSessionId.value = undefined
    }
    loadConversations()
  }

  // 删除指定消息
  async function deleteMessage(messageId: string) {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session?.conversationId) {
        try {
          await deleteSessionMessage(session.conversationId, messageId)
        } catch (e) {
          console.error('Failed to delete message from server:', e)
        }
      }
      if (session) {
        const idx = session.messages.findIndex(m => m.id === messageId)
        if (idx >= 0) {
          session.messages = session.messages.filter(m => m.id !== messageId)
          // 记录墓碑，防止 IndexedDB 缓存把已删消息合并复活
          if (!session.deletedIds.includes(messageId)) {
            session.deletedIds.push(messageId)
          }
          await persistSession(activeSessionId.value)
        }
      }
    }
  }

  return {
    sessions, activeSessionId, conversations, selectedModel, useVectorDb,
    sessionDirectory, setSessionDirectory,
    messages, conversationId, conversationTitle, loading, currentSteps,
    streamPhase, queuePosition,
    retryCountdown,
    send, cancel, clear, undoMessage, deleteConversation, deleteMessage,
    loadConversations, loadConversation, newChat, renameConversation,
    retryLastMessage, manualRetry, cancelAutoRetry,
  }
})
