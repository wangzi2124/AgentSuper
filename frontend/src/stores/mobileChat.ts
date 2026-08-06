import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Message, AgentStep, SSEEvent } from '../types'
import {
  sendMessageStream,
  deleteConversation as apiDeleteConversation,
  deleteMessage as apiDeleteMessage,
  listConversations,
  getConversation,
  renameConversation as apiRenameConversation,
  type ConversationMeta,
} from '../api/chat'
import { interruptSession } from '../api/sessions'

// 支持的模型列表
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
}

export const useMobileChatStore = defineStore('mobileChat', () => {
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
  // 是否显示侧边栏
  const showSidebar = ref(false)
  // 是否显示设置面板
  const showSettings = ref(false)

  // 获取或创建指定 ID 的会话
  function getOrCreateSession(sessionId: string): SessionState {
    if (!sessions.value[sessionId]) {
      sessions.value[sessionId] = {
        messages: [],
        conversationId: undefined,
        conversationTitle: '',
        currentSteps: [],
        loading: false,
        abortController: null,
      }
    }
    return sessions.value[sessionId]
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

  const CONV_TYPE = 'chat'

  // 加载会话列表
  async function loadConversations() {
    try {
      conversations.value = await listConversations(CONV_TYPE)
    } catch (e) {
      console.error('Failed to load conversations:', e)
    }
  }

  // 加载指定会话的消息
  async function loadConversation(id: string) {
    const session = getOrCreateSession(id)
    
    try {
      const detail = await getConversation(id, CONV_TYPE)
      session.conversationId = id
      session.conversationTitle = detail.title
      
      const serverMessages = detail.messages
        .filter((m: { id: string; role: string; content: string }) => !(m.role === 'assistant' && (!m.content || m.content.trim() === '')))
        .map((m: { id: string; role: string; content: string }) => ({
          id: m.id,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: new Date(),
        }))
      
      const localPending = session.messages.filter(localMsg => {
        if (localMsg.role === 'user') {
          return !serverMessages.some((m: Message) => m.id === localMsg.id)
        }
        if (localMsg.role === 'assistant' && localMsg.content === '') {
          return true
        }
        return false
      })
      
      session.messages = [...serverMessages, ...localPending]
      activeSessionId.value = id
      showSidebar.value = false
    } catch (e) {
      console.error('Failed to load conversation:', e)
      activeSessionId.value = id
      showSidebar.value = false
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
      const idx = conversations.value.findIndex((c: ConversationMeta) => c.id === id)
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
    showSidebar.value = false
  }

  // 发送消息（支持 SSE 流式响应）
  async function send(text: string) {
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

    const userMsg: Message = {
      id: genId(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    }
    session.messages = [...session.messages, userMsg]
    session.loading = true
    session.currentSteps = []

    const reqData = {
      message: text,
      conversation_id: session.conversationId,
      model: selectedModel.value,
      use_vector_db: useVectorDb.value,
    }

    const controller = new AbortController()
    session.abortController = controller

    const signal = controller.signal

    try {
      await sendMessageStream(reqData, (event: SSEEvent) => {
        if (signal.aborted) return
        // 尽早记录服务器会话 id（后端在首个事件即注入），保证新会话也能被"停止"打断
        if (event.conversation_id && !session.conversationId) {
          session.conversationId = event.conversation_id
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
        } else if (event.type === 'done') {
          session.conversationId = event.conversation_id
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
        } else if (event.type === 'error') {
          session.messages = [...session.messages, {
            id: genId(),
            role: 'assistant',
            content: `Error: ${event.error || event.detail || 'Unknown error'}`,
            timestamp: new Date(),
          }]
          session.currentSteps = []
        }
      }, signal)
    } catch (err: any) {
      if (!signal.aborted && err.name !== 'AbortError') {
        session.messages = [...session.messages, {
          id: genId(),
          role: 'assistant',
          content: `Error: ${err.message}`,
          timestamp: new Date(),
        }]
        session.currentSteps = []
      }
    } finally {
      session.loading = false
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
      }
    }
  }

  // 删除当前会话
  async function deleteConversation() {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session?.conversationId) {
        try {
          await apiDeleteConversation(session.conversationId, CONV_TYPE)
        } catch (e) {
          console.error('Failed to delete conversation from server:', e)
        }
      }
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
        }
      }
    }
  }

  return {
    sessions, activeSessionId, conversations, selectedModel, useVectorDb,
    showSidebar, showSettings,
    messages, conversationId, conversationTitle, loading, currentSteps,
    send, cancel, clear, deleteConversation, deleteMessage,
    loadConversations, loadConversation, newChat, renameConversation,
  }
})
