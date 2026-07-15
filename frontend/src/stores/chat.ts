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
import { usePermissionStore } from './permission'

export const SUPPORTED_MODELS = [
  { value: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  { value: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'openai/gpt-4o', label: 'OpenAI GPT-4o' },
  { value: 'openai/gpt-4o-mini', label: 'OpenAI GPT-4o-mini' },
] as const

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

interface SessionState {
  messages: Message[]
  conversationId: string | undefined
  conversationTitle: string
  currentSteps: AgentStep[]
  loading: boolean
  abortController: AbortController | null
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<Record<string, SessionState>>({})
  const activeSessionId = ref<string | undefined>(undefined)
  const conversations = ref<ConversationMeta[]>([])
  const selectedModel = ref(SUPPORTED_MODELS[0].value)
  const useVectorDb = ref(true)

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

  const currentSession = computed(() => {
    if (!activeSessionId.value) return null
    return sessions.value[activeSessionId.value] || null
  })

  const messages = computed(() => currentSession.value?.messages || [])
  const conversationId = computed(() => currentSession.value?.conversationId)
  const conversationTitle = computed(() => currentSession.value?.conversationTitle)
  const loading = computed(() => currentSession.value?.loading || false)
  const currentSteps = computed(() => currentSession.value?.currentSteps || [])

  async function loadConversations() {
    try {
      conversations.value = await listConversations()
    } catch (e) {
      console.error('Failed to load conversations:', e)
    }
  }

  async function loadConversation(id: string) {
    const session = getOrCreateSession(id)
    
    // 始终从服务器获取最新数据，确保机器人消息完整
    try {
      const detail = await getConversation(id)
      session.conversationId = id
      session.conversationTitle = detail.title
      
      // 合并本地正在进行的消息（用户已发送但机器人响应未完成）
      const serverMessages = detail.messages.map(m => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        timestamp: new Date(),
      }))
      
      // 保留本地未保存的消息（如正在流式接收的消息）
      const localPending = session.messages.filter(localMsg => {
        // 保留用户消息（可能服务器还没保存）
        if (localMsg.role === 'user') {
          return !serverMessages.some(m => m.id === localMsg.id)
        }
        // 保留正在进行的机器人消息（content 为空表示还在接收）
        if (localMsg.role === 'assistant' && localMsg.content === '') {
          return true
        }
        return false
      })
      
      session.messages = [...serverMessages, ...localPending]
      activeSessionId.value = id
    } catch (e) {
      console.error('Failed to load conversation:', e)
      // 服务器获取失败时，至少设置活跃会话
      activeSessionId.value = id
    }
  }

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

  function newChat() {
    activeSessionId.value = undefined
  }

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

    const reqData = {
      message: text,
      conversation_id: session.conversationId,
      model: selectedModel.value,
      use_vector_db: useVectorDb.value,
      files: files.length > 0 ? files : undefined,
    }

    const controller = new AbortController()
    session.abortController = controller

    const signal = controller.signal

    try {
      await sendMessageStream(reqData, (event: SSEEvent) => {
        if (signal.aborted) return
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

  function cancel() {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session) {
        session.abortController?.abort()
      }
    }
  }

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

  function undoMessage(index: number) {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session) {
        session.messages = session.messages.slice(0, index)
      }
    }
  }

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
      delete sessions.value[activeSessionId.value]
      activeSessionId.value = undefined
      loadConversations()
    }
  }

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
    messages, conversationId, conversationTitle, loading, currentSteps,
    send, cancel, clear, undoMessage, deleteConversation, deleteMessage,
    loadConversations, loadConversation, newChat, renameConversation,
  }
})