<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import MultiAgentResponse from '../components/MultiAgentResponse.vue'
import ChatInput from '../components/ChatInput.vue'

const route = useRoute()
const router = useRouter()
const agent = useMultiAgentStore()
const parentRef = ref<HTMLElement>()
const chatInputRef = ref<any>()
const isNearBottom = ref(true)

const messages = computed(() => agent.messages)

watch(() => messages.value.length, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

watch(() => {
  const msgs = messages.value
  if (msgs.length === 0) return ''
  return msgs[msgs.length - 1]?.content || ''
}, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

onMounted(() => {
  const id = route.params.id as string
  if (id) agent.loadConversation(id)
})

watch(() => route.params.id, (newId) => {
  if (newId) agent.loadConversation(newId as string)
  else agent.newChat()
})

function onScroll() {
  const el = parentRef.value
  if (!el) return
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100
}

function handleSend(text: string) {
  agent.send(text).then(() => {
    if (agent.conversationId && route.name !== 'MultiAgentConversation') {
      router.push({ name: 'MultiAgentConversation', params: { id: agent.conversationId } })
    }
  })
}

function handleCancel() { agent.cancel() }

function handleUndo(index: number) {
  if (agent.loading) agent.cancel()
  const msgText = messages.value[index]?.content
  agent.undoMessage(index)
  if (msgText) {
    chatInputRef.value?.setText(msgText)
    nextTick(() => chatInputRef.value?.focus())
  }
}

function handleMessageDelete(messageId: string) { /* not implemented */ }
</script>

<template>
  <div class="multi-agent-view">
    <div class="chat-header">
      <div>
        <h2>Multi-Agent Supervisor</h2>
        <p>Send a message to all agents simultaneously</p>
      </div>
      <div class="header-controls">
        <span v-if="agent.queuePosition != null" class="stream-badge queued">
          ⏳ 排队中 #{{ agent.queuePosition }}
        </span>
        <span v-else-if="agent.loading" class="stream-badge running">● agents running</span>
      </div>
    </div>

    <div class="chat-body">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="icon">🤖</div>
        <p>Ask a question to all agents</p>
        <p class="hint">Multiple AI agents will process your request in parallel</p>
      </div>

      <div v-else ref="parentRef" class="message-list" @scroll="onScroll">
        <div v-for="(msg, idx) in messages" :key="msg.id" class="message-wrapper">
          <div class="chat-message" :class="[msg.role, { 'is-error': msg.isError }]">
            <div class="avatar">{{ msg.role === 'user' ? '👤' : (msg.isError ? '⚠️' : '🤖') }}</div>
            <div class="bubble">
              <template v-if="msg.role === 'user'">
                <div class="content">{{ msg.content }}</div>
              </template>

              <template v-else>
                <MultiAgentResponse :message="msg" :routingStatus="agent.routingStatus" :isLast="idx === messages.length - 1" />
              </template>

              <div class="message-footer">
                <span class="time">{{ msg.timestamp.toLocaleTimeString() }}</span>
                <div class="message-actions">
                  <button v-if="msg.role === 'user'" class="icon-btn" @click="handleUndo(idx)" title="Undo">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/></svg>
                  </button>
                  <button class="icon-btn delete-btn" @click="handleMessageDelete(msg.id)" title="Delete">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="chat-footer">
      <button v-if="messages.length > 0" class="btn btn-danger" @click="agent.deleteConversation()" style="margin: 0 24px 8px;" :disabled="agent.loading">
        Clear conversation
      </button>
      <ChatInput ref="chatInputRef" :loading="agent.loading" @send="handleSend" @cancel="handleCancel" />
    </div>
  </div>
</template>

<style scoped>
.multi-agent-view { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.chat-header { display: flex; align-items: center; justify-content: space-between; padding: 20px 24px 16px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.chat-header h2 { margin: 0 0 2px; font-size: 20px; }
.chat-header p { margin: 0; font-size: 13px; color: var(--text-secondary); }
.header-controls { display: flex; align-items: center; gap: 16px; flex-shrink: 0; }
.stream-badge { font-size: 12px; padding: 3px 8px; border-radius: 6px; white-space: nowrap; }
.stream-badge.queued { background: rgba(251,191,36,0.12); color: #f59e0b; }
.stream-badge.running { background: rgba(34,197,94,0.12); color: #22c55e; animation: pulse-stream 1.5s ease-in-out infinite; }
@keyframes pulse-stream { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
.chat-body { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.empty-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--text-secondary); }
.empty-state .icon { font-size: 48px; }
.empty-state .hint { font-size: 13px; margin-top: 4px; }
.message-list { flex: 1; overflow-y: auto; padding: 20px 24px; scroll-behavior: smooth; }
.message-list::-webkit-scrollbar { width: 6px; }
.message-list::-webkit-scrollbar-track { background: transparent; }
.message-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.chat-footer { flex-shrink: 0; }
.chat-message { display: flex; gap: 12px; margin-bottom: 16px; }
.chat-message.user { flex-direction: row-reverse; }
.avatar { width: 36px; height: 36px; border-radius: 50%; background: var(--bg); display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.bubble { max-width: 80%; padding: 12px 16px; border-radius: 16px; background: var(--surface); border: 1px solid var(--border); line-height: 1.6; font-size: 14px; }
.user .bubble { background: var(--primary); color: white; border-color: var(--primary); }
.content { white-space: pre-wrap; word-break: break-word; }
.is-error .bubble { background: rgba(239,68,68,0.06); border-color: rgba(239,68,68,0.3); }
.message-footer { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--border); font-size: 11px; }
.user .message-footer { border-top-color: rgba(255,255,255,0.2); }
.time { color: var(--text-secondary); opacity: 0.7; }
.user .time { color: rgba(255,255,255,0.6); }
.message-actions { display: flex; gap: 6px; align-items: center; }
.icon-btn { width: 28px; height: 28px; border-radius: 6px; border: none; background: transparent; color: inherit; opacity: 0.5; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
.icon-btn:hover { opacity: 1; background: rgba(255,255,255,0.1); }
.user .icon-btn:hover { background: rgba(255,255,255,0.2); }
.btn-danger { background: #ef4444; color: #fff; border: none; border-radius: var(--radius); padding: 6px 12px; font-size: 13px; cursor: pointer; }
.btn-danger:hover { background: #dc2626; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
