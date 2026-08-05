import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { MultiAgentMessage, AgentStreamData, MultiAgentSSEEvent, ChatError, AgentStep } from '../types'
import {
  sendMultiAgentStream,
  deleteConversation as apiDeleteConversation,
  deleteMessage as apiDeleteMessage,
  listConversations,
  getConversation,
  renameConversation as apiRenameConversation,
  type ConversationMeta,
} from '../api/multiAgent'
import { SUPPORTED_MODELS } from './chat'

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
  queuePosition: number | null
}

export const useMultiAgentStore = defineStore('multiAgent', () => {
  const sessions = ref<Record<string, SessionState>>({})
  const activeSessionId = ref<string | undefined>(undefined)
  const conversations = ref<ConversationMeta[]>([])
  const routingStatus = ref<string>('')
  const selectedModel = ref(SUPPORTED_MODELS[0].value)
  const useVectorDb = ref(true)

  function getOrCreateSession(sessionId: string, title?: string): SessionState {
    if (!sessions.value[sessionId]) {
      sessions.value[sessionId] = {
        messages: [],
        conversationId: undefined,
        conversationTitle: title || '',
        loading: false,
        abortController: null,
        queuePosition: null,
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
  const queuePosition = computed(() => currentSession.value?.queuePosition ?? null)

  async function loadConversations() {
    try { conversations.value = await listConversations() }
    catch (e) { console.error('Failed to load conversations:', e) }
  }

  async function loadConversation(id: string) {
    const meta = conversations.value.find(c => c.id === id)
    const session = getOrCreateSession(id, meta?.title)
    session.conversationId = id
    if (meta) session.conversationTitle = meta.title
    if (session.loading) { activeSessionId.value = id; return }
    try {
      const detail = await getConversation(id)
      session.conversationTitle = detail.title
      session.messages = detail.messages.map(m => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        agents: m.agents || [],
        timestamp: new Date(),
      }))
      activeSessionId.value = id
    } catch (e) {
      console.error('Failed to load conversation:', e)
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

  function newChat() { routingStatus.value = ''; activeSessionId.value = undefined }

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

    const userMsg: MultiAgentMessage = {
      id: genId(), role: 'user', content: text, agents: [], timestamp: new Date(),
    }
    session.messages = [...session.messages, userMsg]
    session.loading = true
    session.queuePosition = null

    const assistantMsgId = genId()
    const agentsMap: Record<string, AgentStreamData> = {}
    const assistantMsg: MultiAgentMessage = {
      id: assistantMsgId, role: 'assistant', content: '', agents: [], timestamp: new Date(),
    }
    session.messages = [...session.messages, assistantMsg]

    const reqData = { message: text, conversation_id: session.conversationId, model: selectedModel.value, use_vector_db: useVectorDb.value }
    const controller = new AbortController()
    session.abortController = controller
    const signal = controller.signal

    try {
      await sendMultiAgentStream(reqData, (event: MultiAgentSSEEvent) => {
        if (signal.aborted) return

        if (event.type === 'queued') {
          session.queuePosition = event.queue_position ?? null
          return
        }

        // 收到任何执行事件 → 清除排队状态
        if (session.queuePosition !== null) {
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
        } else if (event.type === 'error') {
          routingStatus.value = ''
          assistantMsg.isError = true
          const msg = event.error || event.detail || 'Unknown error'
          assistantMsg.errorInfo = { type: event.retryable ? 'server_error' : 'unknown', message: msg, retryable: !!event.retryable, statusCode: event.status_code }
          assistantMsg.content = `Error: ${msg}`
        } else if (event.type === 'done') {
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
        }
      }, signal)
    } catch (err: any) {
      const isChatError = err && typeof err === 'object' && 'retryable' in err
      const errorInfo: ChatError = isChatError ? err as ChatError : { type: 'unknown', message: String(err), retryable: false }
      if (!signal.aborted) {
        assistantMsg.isError = true; assistantMsg.errorInfo = errorInfo; assistantMsg.content = `Error: ${errorInfo.message}`
        assistantMsg.agents = Object.values(agentsMap)
      }
    } finally {
      routingStatus.value = ''
      session.loading = false
      session.queuePosition = null
      if (session.abortController === controller) session.abortController = null
    }
  }

  function cancel() {
    if (activeSessionId.value) sessions.value[activeSessionId.value]?.abortController?.abort()
  }

  function clear() {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s) { s.messages = []; s.conversationId = undefined; s.conversationTitle = '' }
    }
  }

  // 撤销到指定索引：本地截断 + 尝试同步后端删除（已落库的消息）
  async function undoMessage(index: number) {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (!s) return
      const removed = s.messages.slice(index)
      if (s.conversationId) {
        for (const m of removed) {
          try { await apiDeleteMessage(s.conversationId, m.id) } catch (e) { console.error('Failed to sync undo:', e) }
        }
      }
      s.messages = s.messages.slice(0, index)
    }
  }

  // 删除单条消息：先同步后端（存在会话时），再移除本地
  async function deleteMessage(messageId: string) {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s?.conversationId) {
        try {
          await apiDeleteMessage(s.conversationId, messageId)
        } catch (e) {
          console.error('Failed to delete message from server:', e)
        }
      }
      if (s) s.messages = s.messages.filter(m => m.id !== messageId)
    }
  }

  async function deleteConversation() {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s?.conversationId) { try { await apiDeleteConversation(s.conversationId) } catch (e) { console.error('Failed to delete:', e) } }
      delete sessions.value[activeSessionId.value]
      activeSessionId.value = undefined
      loadConversations()
    }
  }

  return {
    sessions, activeSessionId, conversations, routingStatus, selectedModel, useVectorDb,
    messages, conversationId, conversationTitle, loading, queuePosition,
    send, cancel, clear, undoMessage, deleteMessage, deleteConversation,
    loadConversations, loadConversation, newChat, renameConversation,
  }
})
