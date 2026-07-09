<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useChatStore, SUPPORTED_MODELS } from '../stores/chat'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import StepTaskList from '../components/StepTaskList.vue'

const chat = useChatStore()
const parentRef = ref<HTMLElement>()
const chatInputRef = ref<any>()
const isNearBottom = ref(true)

const messages = computed(() => chat.messages)

watch(() => messages.value.length, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

function onScroll() {
  const el = parentRef.value
  if (!el) return
  const threshold = 100
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

function handleSend(text: string) {
  chat.send(text)
}

function handleCancel() {
  chat.cancel()
}

function handleCopy(_text: string) {
}

function handleUndo(index: number) {
  if (chat.loading) chat.cancel()
  const msgText = messages.value[index]?.content
  chat.messages.splice(index)
  if (msgText) {
    chatInputRef.value?.setText(msgText)
    nextTick(() => chatInputRef.value?.focus())
  }
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
      <button v-if="messages.length > 0" class="btn" @click="chat.clear()" style="margin: 0 24px 8px;" :disabled="chat.loading">
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
</style>
