import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
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

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const conversationId = ref<string | undefined>(undefined)
  const conversationTitle = ref<string>('')
  const conversations = ref<ConversationMeta[]>([])
  const loading = ref(false)
  const selectedModel = ref(SUPPORTED_MODELS[0].value)
  const useVectorDb = ref(true)
  const currentSteps = ref<AgentStep[]>([])
  const abortController = shallowRef<AbortController | null>(null)

  async function loadConversations() {
    try {
      conversations.value = await listConversations()
    } catch (e) {
      console.error('Failed to load conversations:', e)
    }
  }

  async function loadConversation(id: string) {
    try {
      const detail = await getConversation(id)
      conversationId.value = id
      conversationTitle.value = detail.title
      messages.value = detail.messages.map(m => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        timestamp: new Date(),
      }))
    } catch (e) {
      console.error('Failed to load conversation:', e)
    }
  }

  async function renameConversation(id: string, title: string) {
    try {
      await apiRenameConversation(id, title)
      conversationTitle.value = title
      const idx = conversations.value.findIndex(c => c.id === id)
      if (idx >= 0) {
        conversations.value[idx].title = title
      }
    } catch (e) {
      console.error('Failed to rename conversation:', e)
    }
  }

  function newChat() {
    messages.value = []
    conversationId.value = undefined
    conversationTitle.value = ''
    currentSteps.value = []
  }

  async function send(text: string, files: FileContent[] = []) {
    const userMsg: Message = {
      id: genId(),
      role: 'user',
      content: text,
      files: files.map(f => ({ filename: f.filename, mime_type: f.mime_type })),
      timestamp: new Date(),
    }
    messages.value.push(userMsg)
    loading.value = true
    currentSteps.value = []

    const reqData = {
      message: text,
      conversation_id: conversationId.value,
      model: selectedModel.value,
      use_vector_db: useVectorDb.value,
      files: files.length > 0 ? files : undefined,
    }

    const controller = new AbortController()
    abortController.value = controller

    const signal = controller.signal

    try {
      await sendMessageStream(reqData, (event: SSEEvent) => {
        if (signal.aborted) return
        if (event.type === 'step_start' || event.type === 'step_end' ||
            event.type === 'tool_start' || event.type === 'tool_end') {
          const idx = currentSteps.value.findIndex(s => s.step_id === event.step_id)
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
            currentSteps.value[idx] = step
          } else {
            currentSteps.value.push(step)
          }
        } else if (event.type === 'tool_output') {
          const idx = currentSteps.value.findIndex(
            s => s.tool_name === 'tool_execute' && s.status === 'running'
          )
          if (idx >= 0) {
            const step = currentSteps.value[idx]
            const prefix = event.source === 'stderr' ? '[stderr] ' : ''
            const line = prefix + (event.line || '')
            step.tool_output = (step.tool_output || '') + line + '\n'
          }
        } else if (event.type === 'tool_heartbeat') {
          const idx = currentSteps.value.findIndex(
            s => s.tool_name === 'tool_execute' && s.status === 'running'
          )
          if (idx >= 0) {
            const step = currentSteps.value[idx]
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
          conversationId.value = event.conversation_id
          if (event.title) {
            conversationTitle.value = event.title
            loadConversations()
          }
          const assistantMsg: Message = {
            id: event.assistant_msg_id || genId(),
            role: 'assistant',
            content: event.answer || '',
            sources: event.sources,
            steps: event.steps || currentSteps.value,
            timestamp: new Date(),
          }
          messages.value.push(assistantMsg)
          currentSteps.value = []
          if (event.user_msg_id) {
            for (let i = messages.value.length - 1; i >= 0; i--) {
              const m = messages.value[i]
              if (m.role === 'user' && m.id !== event.user_msg_id) {
                m.id = event.user_msg_id
                break
              }
            }
          }
        } else if (event.type === 'error') {
          messages.value.push({
            id: genId(),
            role: 'assistant',
            content: `Error: ${event.error || event.detail || 'Unknown error'}`,
            timestamp: new Date(),
          })
          currentSteps.value = []
        }
      }, signal)
    } catch (err: any) {
      if (!signal.aborted && err.name !== 'AbortError') {
        messages.value.push({
          id: genId(),
          role: 'assistant',
          content: `Error: ${err.message}`,
          timestamp: new Date(),
        })
        currentSteps.value = []
      }
    } finally {
      loading.value = false
      if (abortController.value === controller) {
        abortController.value = null
      }
    }
  }

  function cancel() {
    abortController.value?.abort()
  }

  function clear() {
    messages.value = []
    conversationId.value = undefined
    conversationTitle.value = ''
    currentSteps.value = []
  }

  async function deleteConversation() {
    if (conversationId.value) {
      try {
        await apiDeleteConversation(conversationId.value)
      } catch (e) {
        console.error('Failed to delete conversation from server:', e)
      }
    }
    messages.value = []
    conversationId.value = undefined
    conversationTitle.value = ''
    currentSteps.value = []
    loadConversations()
  }

  async function deleteMessage(messageId: string) {
    if (conversationId.value) {
      try {
        await apiDeleteMessage(conversationId.value, messageId)
      } catch (e) {
        console.error('Failed to delete message from server:', e)
      }
    }
    const idx = messages.value.findIndex(m => m.id === messageId)
    if (idx >= 0) {
      messages.value.splice(idx, 1)
    }
  }

  return {
    messages, conversationId, conversationTitle, conversations,
    loading, selectedModel, useVectorDb, currentSteps,
    send, cancel, clear, deleteConversation, deleteMessage,
    loadConversations, loadConversation, newChat, renameConversation,
  }
})
