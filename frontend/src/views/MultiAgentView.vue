<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import { SUPPORTED_MODELS } from '../config/models'
import type { FileContent } from '../types'
import { usePermissionStore } from '../stores/permission'
import { synthesize, speakNative, stopNative } from '../api/voice'
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
// [F8] 聊天图片点击放大预览（当前预览图的 data URL；空串 = 未预览）
const previewImage = ref('')
const extraWorkspaces = computed(() => perm.workspaces.length > 1 ? perm.workspaces.slice(1) : [])
const mainWorkspace = computed(() => perm.workspaces[0] || '')

const messages = computed(() => agent.messages)

// ── ChatGPT 式滚动交互 ──
// 用户滚动优先：仅「用户手势」滚动（isTrusted=true）更新 isNearBottom，
// 程序滚动（scrollTo 等，isTrusted=false）不打断，避免 smooth 回底中途按钮闪烁。
// 用户离开底部时显示「回到底部」浮动按钮，点击后平滑回底并恢复自动跟随。
const showScrollBtn = computed(() => !isNearBottom.value && messages.value.length > 0)

function scrollToBottom(behavior: ScrollBehavior = 'smooth') {
  const el = parentRef.value
  if (!el) return
  isNearBottom.value = true
  el.scrollTo({ top: el.scrollHeight, behavior })
}

async function checkWeatherPlugin() {
  try {
    const { addAuthHeaders } = await import('../api/fetch')
    const response = await fetch('/api/plugins/weather-alert/status', { headers: await addAuthHeaders() })
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

function onScroll(e: Event) {
  // 程序滚动（scrollTo/scrollBy 触发，isTrusted=false）不参与「离开底部」判定，
  // 避免 smooth 回底中途按钮闪烁；仅用户手势滚动更新 isNearBottom（用户滚动优先）。
  if (!e.isTrusted) return
  const el = parentRef.value
  if (!el) return
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100
}

function handleSend(text: string, files?: FileContent[]) {
  agent.send(text, undefined, files || []).then((completed) => {
    // 仅在真正完成（收到 done）时跳转会话路由并触发 loadConversation，
    // 避免「SSE 断连→重连失败」时导航到服务器 id 新建空会话、把已显示内容顶掉
    if (completed && agent.conversationId && route.name !== 'MultiAgentConversation') {
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

function handleClearConversation() {
  if (!confirm('确定清空当前对话？此操作不可撤销。')) return
  agent.deleteConversation()
}

// [TTS] AI 消息朗读：本地 Qwen3-TTS 合成播放，服务不可达降级浏览器 speechSynthesis
const speakingId = ref<string | null>(null)
let speakAudio: HTMLAudioElement | null = null
async function handleSpeak(id: string, content: string) {
  if (speakingId.value === id) { stopSpeaking(); return }
  stopSpeaking()
  const text = (content || '').trim()
  if (!text) return
  speakingId.value = id
  try {
    const url = await synthesize(text)
    const audio = new Audio(url)
    speakAudio = audio
    audio.onended = () => { if (speakingId.value === id) speakingId.value = null }
    audio.onerror = () => { stopSpeaking(); speakNative(text) }
    await audio.play()
  } catch {
    speakNative(text)
  }
}
function stopSpeaking() {
  if (speakAudio) {
    try { speakAudio.pause() } catch { /* noop */ }
    if (speakAudio.src) URL.revokeObjectURL(speakAudio.src)
    speakAudio = null
  }
  stopNative()
  speakingId.value = null
}
onBeforeUnmount(stopSpeaking)

function handleRetry(messageId: string) {
  agent.manualRetry(messageId)
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
  /* @@CHAT_TABLIST_SCRIPT@@ */
  // ── 会话标签条：聊天框顶部切换 / 新建会话（数据源 = agent.conversations） ──
  agent.loadConversations()

  function switchConversation(id: string) {
    if (id === agent.conversationId) return
    router.push({ name: 'MultiAgentConversation', params: { id } })
  }

  function newConversation() {
    agent.newChat()
    router.push({ name: 'MultiAgent' })
  }
</script>

<template>
  <div class="multi-agent-view">
    <div class="chat-header">
      <div>
        <h2>多智能体编排</h2>
        <p>同时向所有智能体发送消息，并行处理你的请求</p>
        <p v-if="agent.sessionDirectory" class="session-dir" :title="agent.sessionDirectory">
          📁 {{ agent.sessionDirectory }}
        </p>
      </div>
      <div class="header-controls">
        <span v-if="agent.queuePosition != null" class="stream-badge queued">
          ⏳ 排队中 #{{ agent.queuePosition }}
        </span>
        <span v-else-if="agent.loading" class="stream-badge running">● 智能体运行中</span>
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
          <span class="toggle-label">向量库检索</span>
        </label>
        <button
          class="icon-btn clear-chat-btn"
          :disabled="agent.loading || messages.length === 0"
          title="清空当前对话"
          @click="handleClearConversation"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>
      </div>
    </div>
  <!-- @@CHAT_TABLIST@@ -->
  <!-- ── 会话标签条：吸顶在聊天框最上方（移动端展示，桌面端 display:none） ── -->
  <div class="chat-tablist">
    <div
      v-for="c in agent.conversations"
      :key="c.id"
      class="chat-tab"
      :class="{ current: c.id === agent.conversationId }"
      @click="switchConversation(c.id)"
      :title="c.title || '未命名会话'"
    >
      <span class="chat-tab-title">{{ c.title || '未命名会话' }}</span>
    </div>
    <div class="chat-tab chat-tab-new" title="新建会话" @click="newConversation">
      <van-icon name="plus" />
    </div>
  </div>

    <div class="chat-body">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="icon">🤖</div>
        <p>向所有智能体提问</p>
        <p class="hint">多个 AI 智能体将并行处理你的请求</p>
      </div>

      <div v-else ref="parentRef" class="message-list" @scroll="onScroll">
        <div v-for="(msg, idx) in messages" :key="msg.id" class="message-wrapper">
          <div class="chat-message" :class="[msg.role, { 'is-error': msg.isError }]">
            <div class="avatar">{{ msg.role === 'user' ? '👤' : (msg.isError ? '⚠️' : '🤖') }}</div>
            <div class="bubble">
              <template v-if="msg.role === 'user'">
                <div class="content">{{ msg.content }}</div>
                <!-- [F8] 用户消息带附件时回显（图片显示缩略图，其余显示文件 chip） -->
                <div v-if="msg.files && msg.files.length" class="msg-files">
                  <div v-for="(f, fi) in msg.files" :key="fi" class="msg-file">
                    <img
                      v-if="f.mime_type?.startsWith('image/')"
                      :src="f._thumb ? `data:image/jpeg;base64,${f._thumb}` : `data:${f.mime_type};base64,${f.data}`"
                      class="msg-file-image"
                      alt=""
                      @click="previewImage = `data:${f.mime_type};base64,${f.data}`"
                    />
                    <span v-if="f._caption" class="msg-file-caption">{{ f._caption }}</span>
                    <span v-else class="msg-file-name">📄 {{ f.filename }}</span>
                  </div>
                </div>
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
                  <button v-if="msg.role !== 'user'" class="icon-btn speak-btn" :class="{ speaking: speakingId === msg.id }" @click="handleSpeak(msg.id, msg.content)" :title="speakingId === msg.id ? '停止朗读' : '朗读'">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>
                  </button>
                  <button v-if="msg.role === 'user'" class="icon-btn" @click="handleUndo(idx)" title="撤销到此处">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/></svg>
                  </button>
                  <button v-if="msg.isError && msg.errorInfo?.retryable" class="icon-btn" @click="handleRetry(msg.id)" title="重试">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                  </button>
                  <button v-if="msg.isError && agent.retryCountdown > 0" class="retry-countdown" title="自动重试中">
                    ⟳ {{ agent.retryCountdown }}s
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

      <!-- ChatGPT 式「回到底部」：用户离开底部时浮现，点击平滑回底并恢复自动跟随 -->
      <transition name="scroll-fade">
        <button
          v-if="showScrollBtn"
          class="scroll-to-bottom-btn"
          @click="scrollToBottom()"
          title="回到底部"
          aria-label="回到底部"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M19 12l-7 7-7-7"/></svg>
        </button>
      </transition>
    </div>

    <div v-if="agent.notice" class="chat-notice">{{ agent.notice }}</div>

    <div class="chat-footer">
      <ChatInput ref="chatInputRef" :loading="agent.loading" @send="handleSend" @cancel="handleCancel" />
    </div>

    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPick" />

    <!-- [F8] 聊天图片放大预览遮罩：点击图片放大，点击遮罩/图片关闭 -->
    <div v-if="previewImage" class="image-preview-overlay" @click.self="previewImage = ''">
      <img :src="previewImage" class="image-preview-img" alt="预览" @click="previewImage = ''" />
      <span class="image-preview-close" @click="previewImage = ''">✕</span>
    </div>
  </div>
</template>

<style scoped>
.multi-agent-view { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.chat-notice {
  margin: 0 24px 8px; padding: 8px 12px; border-radius: 8px;
  background: var(--accent-soft, rgba(59, 130, 246, 0.12));
  color: var(--text-primary, inherit); font-size: 13px; flex-shrink: 0;
}
.chat-header { display: flex; align-items: center; justify-content: space-between; padding: 20px 24px 16px; border-bottom: 1px solid var(--border); flex-shrink: 0; flex-wrap: wrap; gap: 8px; }
.chat-header h2 { margin: 0 0 2px; font-size: 20px; }
.chat-header p { margin: 0; font-size: 13px; color: var(--text-secondary); }
.chat-header p.session-dir {
  margin-top: 4px;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  color: var(--primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}
@media (max-width: 768px) {
  .chat-header { padding: 16px; }
  .chat-header p.session-dir { max-width: 200px; }
  .message-list { padding: 16px; }
}
.header-controls { display: flex; align-items: center; gap: 12px; flex-shrink: 0; flex-wrap: wrap; }
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
.clear-chat-btn { flex-shrink: 0; }
.clear-chat-btn:hover:not(:disabled) { color: var(--danger, #ef4444); background: color-mix(in srgb, var(--danger, #ef4444) 12%, transparent); opacity: 1; }
.clear-chat-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.speak-btn.speaking { color: var(--primary, #4f46e5); opacity: 1; animation: speak-pulse 1.2s ease-in-out infinite; }
@keyframes speak-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
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
.chat-body { position: relative; flex: 1; overflow: hidden; display: flex; flex-direction: column; }
.empty-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--text-secondary); }
.empty-state .icon { font-size: 48px; }
.empty-state .hint { font-size: 13px; margin-top: 4px; }
.message-list { flex: 1; overflow-y: auto; padding: 20px 24px; scroll-behavior: smooth; max-width: 860px; margin: 0 auto; width: 100%; box-sizing: border-box; }
.message-list::-webkit-scrollbar { width: 6px; }
.message-list::-webkit-scrollbar-track { background: transparent; }
.message-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.chat-footer { flex-shrink: 0; }
.chat-message { display: flex; gap: 12px; margin-bottom: 20px; }
.chat-message.user { flex-direction: row-reverse; }
.avatar { width: 36px; height: 36px; border-radius: 50%; background: var(--bg); display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.bubble { flex: 1; width: 100%; max-width: 100%; padding: 12px 16px; border-radius: 20px; background: var(--surface); border: 1px solid var(--border); line-height: 1.7; font-size: 15px; box-sizing: border-box; border-top-left-radius: 4px; }
.user .bubble { flex: none; width: auto; max-width: 80%; background: var(--bg); color: var(--text); border-color: var(--border); border-top-left-radius: 20px; border-top-right-radius: 4px; }
.content { white-space: pre-wrap; word-break: break-word; }
/* [F8] 用户消息附件回显 */
.msg-files { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.msg-file-image { max-width: 200px; max-height: 200px; border-radius: 8px; object-fit: cover; display: block; cursor: zoom-in; }
.msg-file-caption { display: block; font-size: 12px; color: var(--text-secondary, #64748b); margin-top: 4px; max-width: 200px; }
.msg-file-name { font-size: 12px; padding: 3px 8px; border-radius: 6px; background: rgba(255,255,255,0.18); display: inline-block; }
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
.retry-countdown { border: none; background: transparent; color: #f59e0b; font-size: 12px; font-variant-numeric: tabular-nums; cursor: default; padding: 0 4px; }
.delete-btn:hover { color: #ef4444 !important; }
.btn-danger { background: #ef4444; color: #fff; border: none; border-radius: var(--radius); padding: 6px 12px; font-size: 13px; cursor: pointer; }
.btn-danger:hover { background: #dc2626; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
  /* @@CHAT_SCROLL_INJECTED@@ */
  /* ── ChatGPT 式「回到底部」浮动按钮（用户滚动优先） ── */
  .scroll-to-bottom-btn {
    position: absolute;
    right: 20px;
    bottom: 16px;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 1px solid var(--border, #eef1f6);
    background: var(--surface, #ffffff);
    color: var(--text-secondary, #64748b);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 6px 20px rgba(31, 41, 55, 0.14);
    transition: all 0.2s ease;
    z-index: 20;
    -webkit-tap-highlight-color: transparent;
  }
  .scroll-to-bottom-btn:hover {
    color: var(--primary, #4f46e5);
    transform: translateY(-1px);
    box-shadow: 0 8px 24px rgba(31, 41, 55, 0.18);
  }
  .scroll-to-bottom-btn:active { transform: scale(0.94); }

  /* 进入/离开过渡：淡入 + 上浮 */
  .scroll-fade-enter-active,
  .scroll-fade-leave-active { transition: opacity 0.22s ease, transform 0.22s ease; }
  .scroll-fade-enter-from,
  .scroll-fade-leave-to { opacity: 0; transform: translateY(12px); }

  /* [F8] 聊天图片放大预览遮罩 */
  .image-preview-overlay {
    position: fixed;
    inset: 0;
    z-index: 3000;
    background: rgba(0, 0, 0, 0.82);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
  }
  .image-preview-img {
    max-width: 92vw;
    max-height: 88vh;
    object-fit: contain;
    border-radius: 8px;
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5);
  }
  .image-preview-close {
    position: fixed;
    top: 16px;
    right: 18px;
    font-size: 26px;
    color: #fff;
    cursor: pointer;
    padding: 6px;
    line-height: 1;
    background: rgba(255, 255, 255, 0.12);
    border-radius: 50%;
    width: 34px;
    height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
</style>
