<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChatStore, SUPPORTED_MODELS } from '../stores/chat'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import StepTaskList from '../components/StepTaskList.vue'
import WeatherAlert from '../components/WeatherAlert.vue'

// 路由实例
const route = useRoute()
// 路由器实例
const router = useRouter()
// 聊天状态管理
const chat = useChatStore()
// 消息列表容器引用
const parentRef = ref<HTMLElement>()
// 聊天输入框引用
const chatInputRef = ref<any>()
// 是否滚动到底部
const isNearBottom = ref(true)
// 是否启用天气插件
const isWeatherEnabled = ref(false)

// 计算属性：当前会话的消息列表
const messages = computed(() => chat.messages)

// 侦听消息数量变化，自动滚动到底部
watch(() => messages.value.length, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

// 侦听步骤变化（工具调用过程中），自动滚动到底部
watch(() => chat.currentSteps.length, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

// 侦听最后一条消息的内容变化（流式输出），自动滚动到底部
watch(
  () => {
    const msgs = messages.value
    if (msgs.length === 0) return ''
    return msgs[msgs.length - 1].content
  },
  async () => {
    await nextTick()
    if (isNearBottom.value && parentRef.value) {
      parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
    }
  }
)

// 检查天气插件是否启用
async function checkWeatherPlugin() {
  try {
    const response = await fetch('/api/plugins/weather-alert/status')
    if (response.ok) {
      const data = await response.json()
      isWeatherEnabled.value = data.enabled
    }
  } catch (e) {
    console.error('Failed to check weather plugin status:', e)
  }
}

// 组件挂载时加载指定会话或新建会话
onMounted(() => {
  const id = route.params.id as string
  if (id) {
    chat.loadConversation(id)
  }
  checkWeatherPlugin()
})

// 侦听路由参数变化，加载对应会话或新建会话
watch(() => route.params.id, (newId) => {
  if (newId) {
    chat.loadConversation(newId as string)
  } else {
    chat.newChat()
  }
})

// 滚动事件处理：检测是否滚动到底部附近
function onScroll() {
  const el = parentRef.value
  if (!el) return
  const threshold = 100
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

// 发送消息
function handleSend(text: string) {
  chat.send(text).then(() => {
    if (chat.conversationId && route.name !== 'ChatConversation') {
      router.push({ name: 'ChatConversation', params: { id: chat.conversationId } })
    }
  })
}

// 取消当前请求
function handleCancel() {
  chat.cancel()
}

// 复制消息文本（暂未实现）
function handleCopy(_text: string) {
}

// 撤销指定索引的消息，恢复到输入框
function handleUndo(index: number) {
  if (chat.loading) chat.cancel()
  const msgText = messages.value[index]?.content
  chat.undoMessage(index)
  if (msgText) {
    chatInputRef.value?.setText(msgText)
    nextTick(() => chatInputRef.value?.focus())
  }
}

// 删除指定消息
function handleMessageDelete(messageId: string) {
  chat.deleteMessage(messageId)
}
</script>

<template>
  <div class="chat-view">
    <div class="chat-header">
      <div>
        <h2>Chat with Knowledge Base</h2>
        <p>Ask questions and get answers powered by RAG + AI Agent</p>
      </div>
      <div class="header-controls">
        <span v-if="chat.streamPhase === 'queued'" class="stream-badge queued">
          ⏳ 排队中 #{{ chat.queuePosition }}
        </span>
        <span v-else-if="chat.streamPhase === 'running'" class="stream-badge running">
          ● 流式传输中
        </span>
        <WeatherAlert v-if="isWeatherEnabled" />
        <label class="toggle">
          <input type="checkbox" v-model="chat.useVectorDb" :disabled="chat.loading" />
          <span class="toggle-slider"></span>
          <span class="toggle-label">Vector DB</span>
        </label>
        <div class="model-selector">
          <label for="model-select">Model:</label>
          <select id="model-select" v-model="chat.selectedModel" :disabled="chat.loading">
            <option v-for="m in SUPPORTED_MODELS" :key="m.value" :value="m.value">
              {{ m.label }}
            </option>
          </select>
        </div>
      </div>
    </div>

    <div class="chat-body">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="icon">💬</div>
        <p>Ask a question to get started</p>
        <p class="hint">The AI agent will retrieve relevant documents and answer based on your knowledge base.</p>
      </div>

      <div v-else ref="parentRef" class="message-list" @scroll="onScroll">
        <div v-for="(msg, idx) in messages" :key="msg.id" class="message-wrapper">
          <ChatMessage
            :message="msg"
            :index="idx"
            @copy="handleCopy"
            @undo="handleUndo"
            @delete="handleMessageDelete"
          />
        </div>
        <StepTaskList
          v-if="chat.loading"
          :steps="chat.currentSteps"
          :is-running="chat.loading"
        />
      </div>
    </div>

    <div class="chat-footer">
      <button v-if="messages.length > 0" class="btn btn-danger" @click="chat.deleteConversation()" style="margin: 0 24px 8px;" :disabled="chat.loading">
        Clear conversation
      </button>
      <ChatInput ref="chatInputRef" :loading="chat.loading" @send="handleSend" @cancel="handleCancel" />
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.chat-header h2 {
  margin: 0 0 2px;
  font-size: 20px;
}
.chat-header p {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.header-controls {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
}
.stream-badge {
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 6px;
  white-space: nowrap;
}
.stream-badge.queued {
  background: rgba(251, 191, 36, 0.12);
  color: #f59e0b;
}
.stream-badge.running {
  background: rgba(34, 197, 94, 0.12);
  color: #22c55e;
  animation: pulse-stream 1.5s ease-in-out infinite;
}
@keyframes pulse-stream {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.chat-body {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-secondary);
}
.empty-state .icon { font-size: 48px; }
.empty-state .hint { font-size: 13px; margin-top: 4px; }
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  position: relative;
  scroll-behavior: smooth;
}
.message-list::-webkit-scrollbar {
  width: 6px;
}
.message-list::-webkit-scrollbar-track {
  background: transparent;
}
.message-list::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}
.message-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}
.loading-indicator {
  display: flex;
  gap: 4px;
  padding: 16px 0;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-secondary);
  animation: bounce 1.4s infinite ease-in-out;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}
.chat-footer { flex-shrink: 0; }
.toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary);
  user-select: none;
}
.toggle input { display: none; }
.toggle-slider {
  width: 36px; height: 20px;
  background: var(--border);
  border-radius: 10px;
  position: relative;
  transition: background 0.2s;
}
.toggle-slider::after {
  content: '';
  position: absolute;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: #fff;
  top: 2px; left: 2px;
  transition: transform 0.2s;
}
.toggle input:checked + .toggle-slider { background: var(--primary, #4f46e5); }
.toggle input:checked + .toggle-slider::after { transform: translateX(16px); }
.toggle input:disabled + .toggle-slider { opacity: 0.5; }
.model-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}
.model-selector select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  cursor: pointer;
  min-width: 200px;
}
.model-selector select:focus { border-color: var(--primary); }
.btn-danger {
  background: #ef4444;
  color: #fff;
  border: none;
  border-radius: var(--radius);
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
}
.btn-danger:hover { background: #dc2626; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
