import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
import type { MultiAgentMessage, AgentStreamData, MultiAgentSSEEvent, ChatError, AgentStep } from '../types'
import {
  sendMultiAgentStream,
} from '../api/multiAgent'
import {
  listConversations,
  getConversation,
  renameConversation as apiRenameConversation,
  deleteConversation as apiDeleteConversation,
  type ConversationMeta,
} from '../api/sessions'
import { SUPPORTED_MODELS } from '../config/models'
import {
  saveSessionToCache,
  loadSessionFromCache,
  deleteSessionFromCache,
  mergeServerAndCache,
} from '../api/session-cache'
import { interruptSession, revertSession, deleteSessionMessage } from '../api/sessions'
import { usePermissionStore } from './permission'

function genId(): string {
  try { return crypto.randomUUID() }
  catch { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16) }) }
}

interface SessionState {
  messages: MultiAgentMessage[]
  conversationId: string | undefined
  conversationTitle: string
  loading: boolean
  abortController: AbortController | null
  streamPhase: 'idle' | 'queued' | 'running'
  queuePosition: number | null
  liveMsgId: string | null
  liveContent: string
  deletedIds: string[]
}

export const useMultiAgentStore = defineStore('multiAgent', () => {
  const sessions = ref<Record<string, SessionState>>({})
  const activeSessionId = ref<string | undefined>(undefined)
  const conversations = ref<ConversationMeta[]>([])
  const routingStatus = ref<string>('')
  const selectedModel = ref(SUPPORTED_MODELS[0].value)
  const useVectorDb = ref(false)
  // 当前/新建会话绑定的工作目录（opencode ctx.directory）。首条消息发送时
  // 随请求 directory 创建会话；已有会话在 loadConversation 时同步为服务器值。
  const sessionDirectory = ref('')

  // --- 重试机制 ---
  const AUTO_RETRY_DELAY = 5 // 秒
  const MAX_AUTO_RETRIES = 2
  let autoRetryTimer: ReturnType<typeof setTimeout> | null = null
  // [S8] 独立自动重试计数：startAutoRetry 自增，用户主动新发 / 成功 done 清零。
  // 替代旧的「数尾部连续 error 消息」方式——手动重试成功后计数不再残留。
  let retryCount = 0
  // 自动重试重发时置位：send 开头跳过 cancelAutoRetry() 的计数清零，
  // 让 S8 独立计数在连发重试之间持续累加（否则每次重发都被清零、永远达不到上限）。
  let suppressRetryReset = false
  const retryCountdown = ref(0)
  const autoRetrySessionId = ref<string | undefined>(undefined)
  const retryMessageText = ref('')

  function setSessionDirectory(dir: string) {
    sessionDirectory.value = dir || ''
  }

  function getOrCreateSession(sessionId: string, title?: string): SessionState {
    if (!sessions.value[sessionId]) {
      sessions.value[sessionId] = {
        messages: [],
        conversationId: undefined,
        conversationTitle: title || '',
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

  // 将会话消息持久化到 IndexedDB（过滤 live 占位 + 墓碑）
  async function persistSession(sessionId: string) {
    const session = sessions.value[sessionId]
    if (!session) return
    const messages = session.messages.filter(m => !m.live)
    const cacheKey = session.conversationId || sessionId
    await saveSessionToCache<MultiAgentMessage>(
      cacheKey,
      messages,
      session.conversationId,
      session.conversationTitle,
      session.deletedIds,
    )
    if (cacheKey !== sessionId) {
      await deleteSessionFromCache(sessionId)
    }
  }

  const currentSession = computed(() => {
    if (!activeSessionId.value) return null
    return sessions.value[activeSessionId.value] || null
  })

  const messages = computed(() => currentSession.value?.messages || [])
  const conversationId = computed(() => currentSession.value?.conversationId)
  const conversationTitle = computed(() => currentSession.value?.conversationTitle)
  const loading = computed(() => currentSession.value?.loading || false)
  const streamPhase = computed(() => currentSession.value?.streamPhase || 'idle')
  const queuePosition = computed(() => currentSession.value?.queuePosition ?? null)

  async function loadConversations() {
    try { conversations.value = await listConversations('multi-agent') }
    catch (e) { console.error('Failed to load conversations:', e) }
  }

  async function loadConversation(id: string) {
    const meta = conversations.value.find(c => c.id === id)
    const session = getOrCreateSession(id, meta?.title)
    session.conversationId = id
    if (meta) session.conversationTitle = meta.title
    // 会话正在流式 → 保留本地消息，不覆盖
    if (session.loading || session.streamPhase !== 'idle') { activeSessionId.value = id; return }
    try {
      const detail = await getConversation(id, 'multi-agent')
      session.conversationTitle = detail.title
      // 同步会话绑定目录（服务器为准；无论空与非空都覆盖，避免残留上一会话的目录）
      sessionDirectory.value = detail.directory || ''
      const serverMessages: MultiAgentMessage[] = detail.messages.map(m => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        agents: (m as any).agents || [],
        timestamp: new Date(),
      }))
      // 从 IndexedDB 加载本地缓存（SSE 中断时可能有未同步消息）
      const cached = await loadSessionFromCache<MultiAgentMessage>(id)
      session.deletedIds = cached?.deletedIds || []
      session.messages = mergeServerAndCache(serverMessages, cached?.messages || [], session.deletedIds)
      await saveSessionToCache(id, session.messages, id, detail.title, session.deletedIds)
      activeSessionId.value = id
    } catch (e) {
      console.error('Failed to load conversation:', e)
      // 服务器获取失败，尝试从 IndexedDB 恢复
      const cached = await loadSessionFromCache<MultiAgentMessage>(id)
      if (cached) {
        session.deletedIds = cached.deletedIds || []
        const tomb = new Set(session.deletedIds)
        session.messages = cached.messages.filter(m => !m.live && !tomb.has(m.id))
        session.conversationTitle = cached.conversationTitle || session.conversationTitle
      }
      activeSessionId.value = id
    }
  }

  async function renameConversation(id: string, title: string) {
    try {
      await apiRenameConversation(id, title)
      const session = sessions.value[id]
      if (session) session.conversationTitle = title
      const idx = conversations.value.findIndex(c => c.id === id)
      if (idx >= 0) conversations.value[idx].title = title
    } catch (e) { console.error('Failed to rename:', e) }
  }

  function newChat() {
    routingStatus.value = ''
    activeSessionId.value = undefined
    sessionDirectory.value = ''
  }

  // --- 错误分类 ---

  function classifySSEError(event: MultiAgentSSEEvent): ChatError {
    const detail = event.error || event.detail || 'Unknown error'
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
    if (lower.includes('abort') || lower.includes('aborted')) return { type: 'unknown', message: msg, retryable: false }
    if (lower.includes('timeout') || lower.includes('timed out')) return { type: 'timeout', message: msg, retryable: true }
    if (lower.includes('rate limit') || lower.includes('429') || lower.includes('too many requests')) return { type: 'rate_limit', message: msg, retryable: true }
    if (lower.includes('failed to fetch') || lower.includes('networkerror')) return { type: 'network', message: msg, retryable: true }
    if (lower.includes('500') || lower.includes('502') || lower.includes('503') || lower.includes('504')) return { type: 'server_error', message: msg, retryable: true }
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

    // [S8] 独立重试计数：达到上限则放弃（cancelAutoRetry 会清计数，属终止路径）
    if (retryCount >= MAX_AUTO_RETRIES) {
      cancelAutoRetry()
      return
    }

    // 清掉上个 timer（只清 timer，不清计数——不能走 cancelAutoRetry）
    if (autoRetryTimer) {
      clearInterval(autoRetryTimer)
      autoRetryTimer = null
    }
    retryCount++
    retryCountdown.value = AUTO_RETRY_DELAY
    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    autoRetryTimer = setInterval(() => {
      retryCountdown.value--
      if (retryCountdown.value <= 0) {
        if (autoRetryTimer) {
          clearInterval(autoRetryTimer)
          autoRetryTimer = null
        }
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
    // [S8] 取消/成功/主动新发一律清零独立重试计数
    retryCount = 0
  }

  // 重试最后一条用户消息
  async function retryLastMessage() {
    const sessionId = autoRetrySessionId.value || activeSessionId.value
    if (!sessionId) return
    const session = sessions.value[sessionId]
    if (!session) return

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

    // [S2] 重试复用原 user 消息的 client_msg_id，服务端按幂等键去重，不产生重复轮次
    // [S8] 置位抑制 send 开头的计数清零，让独立重试计数在连发间持续累加
    suppressRetryReset = true
    await send(lastUserMsg.content, lastUserMsg.clientMsgId)
  }

  // 手动重试（从 error 消息的 UI 触发）
  async function manualRetry(messageId: string) {
    if (!activeSessionId.value) return
    const session = sessions.value[activeSessionId.value]
    if (!session) return

    cancelAutoRetry()

    const errorIdx = session.messages.findIndex(m => m.id === messageId)
    if (errorIdx < 0) return

    let userIdx = -1
    for (let i = errorIdx - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') {
        userIdx = i
        break
      }
    }
    if (userIdx < 0) return

    const userMsg = session.messages[userIdx]
    session.messages = session.messages.slice(0, userIdx)
    // [S2] 手动重试同样复用原 user 消息的 client_msg_id，服务端幂等去重
    await send(userMsg.content, userMsg.clientMsgId)
  }

  // 把内存会话的 key 从客户端 genId 迁移到服务器 conversation_id。
  // 只能成功完成（done）或失败（error/断连）后调用：一旦知道服务器 id，
  // 就将内存里的完整消息（含流式中断的部分内容）挂到服务器 id 下，
  // 避免之后 loadConversation(serverId) 因找不到内存会话而新建空会话，
  // 导致「断连后重连内容丢失、要重新访问后台」。
  function migrateToServerId(sessionId: string) {
    const session = sessions.value[sessionId]
    if (!session) return
    const serverId = session.conversationId
    if (!serverId || serverId === sessionId) return
    if (!sessions.value[serverId]) {
      sessions.value[serverId] = session
    } else if (sessions.value[serverId] !== session) {
      // 已存在不同对象：把当前消息合并进已存在会话（保留内容最多的）
      const target = sessions.value[serverId]
      if (session.messages.length > target.messages.length) {
        target.messages = session.messages
        target.conversationTitle = session.conversationTitle
      }
    }
    delete sessions.value[sessionId]
    if (activeSessionId.value === sessionId) activeSessionId.value = serverId
    if (autoRetrySessionId.value === sessionId) autoRetrySessionId.value = serverId
  }

  async function send(text: string, clientMsgId?: string): Promise<boolean> {
    let sessionId = activeSessionId.value
    if (!sessionId) {
      sessionId = genId()
      getOrCreateSession(sessionId)
      activeSessionId.value = sessionId
    }

    const session = sessions.value[sessionId]
    if (!session) return false
    // 防止同一会话并发发送导致消息/步骤竞态
    if (session.loading) return false
    if (suppressRetryReset) {
      // [S8] 自动重试重发：只清 timer，保留独立重试计数（在连发间持续累加）
      if (autoRetryTimer) {
        clearInterval(autoRetryTimer)
        autoRetryTimer = null
      }
      retryCountdown.value = 0
      suppressRetryReset = false
    } else {
      // 新的/手动发送：完整取消自动重试（含清零独立重试计数）
      cancelAutoRetry()
    }

    let completed = false

    // [S2] 幂等 id：重试时复用原 user 消息的 clientMsgId，首次发送则新生成
    const messageClientId = clientMsgId ?? genId()
    const userMsg: MultiAgentMessage = {
      id: genId(), role: 'user', content: text, agents: [], timestamp: new Date(),
      clientMsgId: messageClientId,
    }
    session.messages = [...session.messages, userMsg]
    session.loading = true
    session.streamPhase = 'queued'
    session.queuePosition = null

    const assistantMsgId = genId()
    const agentsMap: Record<string, AgentStreamData> = {}
    const assistantMsg: MultiAgentMessage = reactive({
      id: assistantMsgId, role: 'assistant', content: '', agents: [], timestamp: new Date(),
    })
    session.messages = [...session.messages, assistantMsg]

    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    // 发送后立即持久化（SSE 中断也不丢失 user 消息）
    persistSession(sessionId)

    const reqData = { message: text, conversation_id: session.conversationId, model: selectedModel.value, use_vector_db: useVectorDb.value, directory: sessionDirectory.value || undefined, client_msg_id: messageClientId }
    const controller = new AbortController()
    session.abortController = controller
    const signal = controller.signal

    try {
      await sendMultiAgentStream(reqData, (event: MultiAgentSSEEvent) => {
        if (signal.aborted) return

        // 尽早记录服务器会话 id（后端在首个事件即注入），保证新会话也能被"停止"打断
        if (event.conversation_id && !session.conversationId) {
          session.conversationId = event.conversation_id
        }

        if (event.type === 'queued') {
          session.streamPhase = 'queued'
          session.queuePosition = event.queue_position ?? null
          return
        }

        // 收到任何执行事件 → 运行阶段
        if (session.streamPhase !== 'running') {
          session.streamPhase = 'running'
          session.queuePosition = null
        }

        if (event.type === 'routing') {
          routingStatus.value = event.detail || 'Routing...'
        } else if (event.type === 'agent_start') {
          routingStatus.value = ''
          agentsMap[event.agent_id] = {
            agent_id: event.agent_id,
            agent_name: event.agent_name || event.agent_id,
            agent_avatar: event.agent_avatar,
            status: 'running',
            content: '',
            steps: [],
          }
          assistantMsg.agents = Object.values(agentsMap)
        } else if (event.type === 'agent_step' && event.step && event.step.step_id) {
          const agent = agentsMap[event.agent_id]
          if (agent) {
            const i = agent.steps.findIndex(s => s.step_id === event.step!.step_id)
            if (i >= 0) agent.steps[i] = event.step
            else agent.steps.push(event.step)
            assistantMsg.agents = Object.values(agentsMap)
          }
        } else if (event.type === 'agent_stream') {
          const agent = agentsMap[event.agent_id]
          if (agent && event.content) { agent.content += event.content; assistantMsg.agents = Object.values(agentsMap) }
        } else if (event.type === 'agent_done') {
          const agent = agentsMap[event.agent_id]
          if (agent) { agent.status = 'completed'; if (event.content) agent.content = event.content; assistantMsg.agents = Object.values(agentsMap) }
        } else if (event.type === 'agent_error') {
          const agent = agentsMap[event.agent_id]
          if (agent) { agent.status = 'failed'; agent.error = event.error; assistantMsg.agents = Object.values(agentsMap) }
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
        } else if (event.type === 'error') {
          session.streamPhase = 'idle'
          routingStatus.value = ''
          assistantMsg.isError = true
          const errorInfo = classifySSEError(event)
          assistantMsg.errorInfo = errorInfo
          assistantMsg.content = formatErrorMessage(errorInfo)
          persistSession(sessionId)
          // 自动重试：仅对可重试错误且未超过最大次数
          if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
            startAutoRetry(sessionId, text)
          }
        } else if (event.type === 'done') {
          completed = true
          session.streamPhase = 'idle'
          routingStatus.value = ''
          session.conversationId = event.conversation_id
          if (event.title) { session.conversationTitle = event.title; loadConversations() }
          if (event.answer) assistantMsg.content = event.answer
          else if (event.content) assistantMsg.content = event.content
          // 回填服务器生成的消息 id，保证删除/撤销能命中真实消息
          if (event.assistant_msg_id) assistantMsg.id = event.assistant_msg_id
          if (event.user_msg_id) {
            for (let i = session.messages.length - 1; i >= 0; i--) {
              const m = session.messages[i]
              if (m.role === 'user' && m.id !== event.user_msg_id) {
                m.id = event.user_msg_id
                break
              }
            }
          }
          // 兜底：直播事件缺失时用后端快照回填 agent 面板（如重连/丢事件）
          if (Object.keys(agentsMap).length === 0 && event.agents?.length) {
            assistantMsg.agents = event.agents
          } else {
            assistantMsg.agents = Object.values(agentsMap)
          }
          // 完成后持久化 + 内存 key 迁移（客户端 genId → 服务器 id）
          persistSession(sessionId)
          migrateToServerId(sessionId)
          cancelAutoRetry()
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
      const isChatError = err && typeof err === 'object' && 'retryable' in err
      const errorInfo: ChatError = isChatError ? err as ChatError : classifyNetworkError(err)
      if (!signal.aborted) {
        assistantMsg.isError = true; assistantMsg.errorInfo = errorInfo; assistantMsg.content = formatErrorMessage(errorInfo)
        assistantMsg.agents = Object.values(agentsMap)
        persistSession(sessionId)
        // 失败/断连后也已拿到服务器 id：立刻迁移内存 key，避免后续
        // loadConversation(serverId) 新建空会话把内容顶掉
        migrateToServerId(sessionId)
        // 自动重试：仅对可重试错误且未超过最大次数
        if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
          startAutoRetry(sessionId, text)
        }
      }
    } finally {
      routingStatus.value = ''
      session.loading = false
      session.streamPhase = 'idle'
      session.queuePosition = null
      if (session.abortController === controller) session.abortController = null
    }
    return completed
  }

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

  function clear() {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s) { s.messages = []; s.conversationId = undefined; s.conversationTitle = ''; s.deletedIds = [] }
      deleteSessionFromCache(activeSessionId.value)
    }
  }

  // 撤销到指定索引：保留 [0, index)，删除其后所有消息（走 /sessions/{id}/revert）
  async function undoMessage(index: number) {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (!s) return
      // 目标保留消息 = 撤销点前一条；index===0 无保留消息，仅本地清空
      const target = index > 0 ? s.messages[index - 1] : undefined
      const removedIds = s.messages.slice(index).map(m => m.id)
      if (s.conversationId && target) {
        try { await revertSession(s.conversationId, target.id) } catch (e) { console.error('Failed to sync undo:', e) }
      }
      s.messages = s.messages.slice(0, index)
      // 记录墓碑，防止 IndexedDB 缓存把已撤销消息合并复活
      if (removedIds.length) {
        s.deletedIds = [...new Set([...s.deletedIds, ...removedIds])]
      }
      await persistSession(activeSessionId.value)
    }
  }

  // 删除单条消息：先同步后端（存在会话时），再移除本地
  async function deleteMessage(messageId: string) {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s?.conversationId) {
        try {
          await deleteSessionMessage(s.conversationId, messageId)
        } catch (e) {
          console.error('Failed to delete message from server:', e)
        }
      }
      if (s) {
        s.messages = s.messages.filter(m => m.id !== messageId)
        if (!s.deletedIds.includes(messageId)) s.deletedIds.push(messageId)
        await persistSession(activeSessionId.value)
      }
    }
  }

  async function deleteConversation() {
    if (activeSessionId.value) {
      cancelAutoRetry()
      const s = sessions.value[activeSessionId.value]
      s?.abortController?.abort()
      const serverId = s?.conversationId || activeSessionId.value
      if (serverId) { try { await apiDeleteConversation(serverId) } catch (e) { console.error('Failed to delete:', e) } }
      await deleteSessionFromCache(serverId)
      if (serverId !== activeSessionId.value) await deleteSessionFromCache(activeSessionId.value)
      delete sessions.value[activeSessionId.value]
      activeSessionId.value = undefined
      loadConversations()
    }
  }

  return {
    sessions, activeSessionId, conversations, routingStatus, selectedModel, useVectorDb,
    sessionDirectory, setSessionDirectory,
    messages, conversationId, conversationTitle, loading, streamPhase, queuePosition,
    retryCountdown,
    send, cancel, clear, undoMessage, deleteMessage, deleteConversation,
    loadConversations, loadConversation, newChat, renameConversation,
    retryLastMessage, manualRetry, cancelAutoRetry,
  }
})
