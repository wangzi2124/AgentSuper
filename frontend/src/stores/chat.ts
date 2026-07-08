import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
import type { FileContent, Message, AgentStep, SSEEvent } from '../types'
import { sendMessageStream } from '../api/chat'

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
  const loading = ref(false)
  const selectedModel = ref(SUPPORTED_MODELS[0].value)
  const useVectorDb = ref(true)
  const currentSteps = ref<AgentStep[]>([])
  const abortController = shallowRef<AbortController | null>(null)

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
          }
          if (idx >= 0) {
            currentSteps.value[idx] = step
          } else {
            currentSteps.value.push(step)
          }
        } else if (event.type === 'done') {
          conversationId.value = event.conversation_id
          const assistantMsg: Message = {
            id: genId(),
            role: 'assistant',
            content: event.answer || '',
            sources: event.sources,
            steps: event.steps || currentSteps.value,
            timestamp: new Date(),
          }
          messages.value.push(assistantMsg)
          currentSteps.value = []
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
    currentSteps.value = []
  }

  return { messages, conversationId, loading, selectedModel, useVectorDb, currentSteps, send, cancel, clear }
})
