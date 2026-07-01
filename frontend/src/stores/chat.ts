import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { FileContent, Message } from '../types'
import { sendMessage } from '../api/chat'

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

    try {
      const res = await sendMessage({
        message: text,
        conversation_id: conversationId.value,
        model: selectedModel.value,
        use_vector_db: useVectorDb.value,
        files: files.length > 0 ? files : undefined,
      })
      conversationId.value = res.conversation_id
      const assistantMsg: Message = {
        id: genId(),
        role: 'assistant',
        content: res.answer,
        sources: res.sources,
        timestamp: new Date(),
      }
      messages.value.push(assistantMsg)
    } catch (err: any) {
      messages.value.push({
        id: genId(),
        role: 'assistant',
        content: `Error: ${err.message}`,
        timestamp: new Date(),
      })
    } finally {
      loading.value = false
    }
  }

  function clear() {
    messages.value = []
    conversationId.value = undefined
  }

  return { messages, conversationId, loading, selectedModel, useVectorDb, send, clear }
})
