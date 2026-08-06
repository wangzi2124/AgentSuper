<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import { SUPPORTED_MODELS } from '../stores/chat'
import { usePermissionStore } from '../stores/permission'
import MultiAgentResponse from '../components/MultiAgentResponse.vue'
import ChatInput from '../components/ChatInput.vue'
import WeatherAlert from '../components/WeatherAlert.vue'
import DirPickerModal from '../components/DirPickerModal.vue'

const route = useRoute()
const router = useRouter()
const agent = useMultiAgentStore()
const perm = usePermissionStore()
const parentRef = ref<HTMLElement>()
const chatInputRef = ref<any>()
const isNearBottom = ref(true)
const isWeatherEnabled = ref(false)
const showWsPanel = ref(false)
const wsInput = ref('')
const wsError = ref('')
const wsBusy = ref(false)
const showDirPicker = ref(false)
const extraWorkspaces = computed(() => perm.workspaces.length > 1 ? perm.workspaces.slice(1) : [])
const mainWorkspace = computed(() => perm.workspaces[0] || '')

const messages = computed(() => agent.messages)

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

function toggleWsPanel() {
  showWsPanel.value = !showWsPanel.value
  wsError.value = ''
}

async function handleAddWorkspace() {
  const path = wsInput.value.trim()
  if (!path) {
    wsError.value = '请输入绝对路径，如 F:\\tetris'
    return
  }
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.addWorkspace(path)
    wsInput.value = ''
  } catch (e: any) {
    wsError.value = e?.message || '添加失败'
  } finally {
    wsBusy.value = false
  }
}

async function handleRemoveWorkspace(path: string) {
  try {
    await perm.removeWorkspace(path)
  } catch (e: any) {
    wsError.value = e?.message || '移除失败'
  }
}

// 目录选择器选中后回填输入框
function handleDirPick(path: string) {
  wsInput.value = path
  showDirPicker.value = false
  wsError.value = ''
}

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

watch(() => {
  const msgs = messages.value
  if (msgs.length === 0) return 0
  return msgs[msgs.length - 1]?.agents?.reduce((n, a) => n + (a.steps?.length || 0), 0) ?? 0
}, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

onMounted(() => {
  const id = route.params.id as string
  if (id) agent.loadConversation(id)
  checkWeatherPlugin()
  perm.loadWorkspaces()
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

function handleMessageDelete(messageId: string) {
  if (agent.loading) agent.cancel()
  agent.deleteMessage(messageId)
}

const copiedId = ref<string | null>(null)

async function handleCopy(messageId: string, text: string) {
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    copiedId.value = messageId
    setTimeout(() => { if (copiedId.value === messageId) copiedId.value = null }, 1500)
  } catch (e) {
    console.error('Copy failed:', e)
  }
}
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
        <WeatherAlert v-if="isWeatherEnabled" />
        <div class="ws-manager">
          <button class="ws-btn" @click="toggleWsPanel" title="管理可写工作目录">
            📁 工作目录 ({{ extraWorkspaces.length }})
          </button>
          <div v-if="showWsPanel" class="ws-panel">
            <div class="ws-row">
              <button class="ws-pick-btn" title="选择目录" @click="showDirPicker = true">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
              </button>
              <input
                v-model="wsInput"
                class="ws-input"
                placeholder="F:\tetris"
                @keyup.enter="handleAddWorkspace"
              />
              <button class="ws-add" :disabled="wsBusy" @click="handleAddWorkspace">
                {{ wsBusy ? '添加中...' : '添加' }}
              </button>
            </div>
            <p v-if="wsError" class="ws-error">{{ wsError }}</p>
            <div class="ws-list">
              <div v-if="mainWorkspace" class="ws-item fixed" :title="mainWorkspace">
                <span class="ws-dot"></span>
                <span class="ws-path">{{ mainWorkspace }}</span>
                <span class="ws-tag">主</span>
              </div>
              <div v-for="w in extraWorkspaces" :key="w" class="ws-item">
                <span class="ws-dot"></span>
                <span class="ws-path">{{ w }}</span>
                <button class="ws-remove" title="移除" @click="handleRemoveWorkspace(w)">×</button>
              </div>
              <p v-if="extraWorkspaces.length === 0" class="ws-empty">
                无额外工作区。添加后 Agent 可写该路径（无需重启）。
              </p>
            </div>
          </div>
        </div>
        <label class="toggle">
          <input type="checkbox" v-model="agent.useVectorDb" :disabled="agent.loading" />
          <span class="toggle-slider"></span>
          <span class="toggle-label">Vector DB</span>
        </label>
        <div class="model-selector">
          <label for="model-select">Model:</label>
          <select id="model-select" v-model="agent.selectedModel" :disabled="agent.loading">
            <option v-for="m in SUPPORTED_MODELS" :key="m.value" :value="m.value">
              {{ m.label }}
            </option>
          </select>
        </div>
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
                  <div class="btn-wrapper">
                    <button class="icon-btn" @click="handleCopy(msg.id, msg.content)" title="复制">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    </button>
                    <span v-if="copiedId === msg.id" class="copy-toast">复制成功</span>
                  </div>
                  <button v-if="msg.role === 'user'" class="icon-btn" @click="handleUndo(idx)" title="撤销到此处">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/></svg>
                  </button>
                  <button class="icon-btn delete-btn" @click="handleMessageDelete(msg.id)" title="删除消息">
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

    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPick" />
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
.ws-manager {
  position: relative;
  flex-shrink: 0;
}
.ws-btn {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.ws-btn:hover { border-color: var(--primary); }
.ws-panel {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 6px;
  width: 360px;
  max-width: 70vw;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  padding: 12px;
  z-index: 50;
}
.ws-row {
  display: flex;
  gap: 8px;
}
.ws-pick-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.15s;
}
.ws-pick-btn:hover { border-color: var(--primary); color: var(--primary); }
.ws-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  outline: none;
}
.ws-input:focus { border-color: var(--primary); }
.ws-add {
  padding: 6px 12px;
  border: none;
  border-radius: var(--radius);
  background: var(--primary, #4f46e5);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.ws-add:disabled { opacity: 0.5; cursor: not-allowed; }
.ws-error { color: #ef4444; font-size: 12px; margin: 6px 0 0; }
.ws-list {
  margin-top: 10px;
  max-height: 200px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ws-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text);
}
.ws-item.fixed { opacity: 0.7; }
.ws-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  flex-shrink: 0;
}
.ws-path {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'Cascadia Code', Consolas, monospace;
}
.ws-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(79, 70, 229, 0.1);
  color: var(--primary, #4f46e5);
  flex-shrink: 0;
}
.ws-remove {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
  flex-shrink: 0;
}
.ws-remove:hover { color: #ef4444; }
.ws-empty { font-size: 12px; color: var(--text-secondary); margin: 0; }
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
.btn-wrapper {
  position: relative;
  display: inline-flex;
}
.copy-toast {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 4px);
  transform: translateX(-50%);
  white-space: nowrap;
  font-size: 11px;
  background: var(--text);
  color: var(--bg);
  padding: 2px 8px;
  border-radius: 4px;
  pointer-events: none;
  animation: fadeInOut 1.5s ease-in-out;
}
@keyframes fadeInOut {
  0% { opacity: 0; transform: translateX(-50%) translateY(4px); }
  15% { opacity: 1; transform: translateX(-50%) translateY(0); }
  85% { opacity: 1; transform: translateX(-50%) translateY(0); }
  100% { opacity: 0; transform: translateX(-50%) translateY(-4px); }
}
.icon-btn { width: 28px; height: 28px; border-radius: 6px; border: none; background: transparent; color: inherit; opacity: 0.5; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
.icon-btn:hover { opacity: 1; background: rgba(255,255,255,0.1); }
.user .icon-btn:hover { background: rgba(255,255,255,0.2); }
.delete-btn:hover { color: #ef4444 !important; }
.btn-danger { background: #ef4444; color: #fff; border: none; border-radius: var(--radius); padding: 6px 12px; font-size: 13px; cursor: pointer; }
.btn-danger:hover { background: #dc2626; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
