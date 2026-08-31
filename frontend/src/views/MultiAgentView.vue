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
const showWeather = ref(false)
const showSettings = ref(false)
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
  if (!e.isTrusted) return
  const el = parentRef.value
  if (!el) return
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100
}

function handleSend(text: string, files?: FileContent[]) {
  markPendingAutoRead()
  agent.send(text, undefined, files || []).then((completed) => {
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

function openWeather() {
  showSettings.value = false
  showWeather.value = true
}

// [TTS] AI 消息朗读
const speakingId = ref<string | null>(null)
let speakAudio: HTMLAudioElement | null = null

const AUTO_TTS_KEY = 'agent_super_auto_tts'
let autoReadInit = false
try { autoReadInit = localStorage.getItem(AUTO_TTS_KEY) === '1' } catch { /* noop */ }
const autoRead = ref(autoReadInit)
watch(autoRead, v => { try { localStorage.setItem(AUTO_TTS_KEY, v ? '1' : '0') } catch { /* noop */ } })
let pendingAutoRead = false
function markPendingAutoRead() { if (autoRead.value) pendingAutoRead = true }
watch(() => agent.loading, (loading) => {
  if (loading || !pendingAutoRead) return
  pendingAutoRead = false
  const last = agent.messages[agent.messages.length - 1]
  if (!last || last.role !== 'assistant' || last.isError || !last.content) return
  const text = last.content.trim()
  if (text) handleSpeak(last.id, text)
})

async function handleSpeak(id: string, content: string) {
  if (speakingId.value === id) { stopSpeaking(); return }
  stopSpeaking()
  let text = (content || '').trim()
  if (!text) return
  const MAX_TTS_CHARS = 1200
  if (text.length > MAX_TTS_CHARS) text = text.slice(0, MAX_TTS_CHARS) + '。'
  speakingId.value = id
  try {
    const url = await synthesize(text)
    const audio = new Audio(url)
    speakAudio = audio
    audio.onended = () => { if (speakingId.value === id) speakingId.value = null }
    audio.onerror = () => { stopSpeaking(); speakNative((content || '').trim()) }
    await audio.play()
  } catch {
    speakNative((content || '').trim())
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
      <div class="chat-heading">
        <h2>多智能体编排</h2>
        <p>同时向所有智能体发送消息，并行处理你的请求</p>
      </div>
      <div class="header-actions">
        <div v-if="agent.queuePosition != null" class="status-badge queued">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          排队 #{{ agent.queuePosition }}
        </div>
        <div v-else-if="agent.loading" class="status-badge running">
          <span class="pulse-dot"></span> 运行中
        </div>
        <button
          class="icon-btn settings-toggle"
          :class="{ active: showSettings }"
          title="设置"
          @click="showSettings = !showSettings"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        </button>
      </div>
    </div>

    <!-- 设置抽屉：收纳所有次要功能 -->
    <transition name="drawer-fade">
      <div v-if="showSettings" class="settings-backdrop" @click.self="showSettings = false"></div>
    </transition>
    <transition name="drawer-slide">
      <aside v-if="showSettings" class="settings-drawer">
        <div class="drawer-head">
          <span class="drawer-title">对话设置</span>
          <button class="drawer-close" title="关闭" @click="showSettings = false">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <div class="drawer-body">
          <!-- 模型 -->
          <div class="drawer-section">
            <div class="drawer-label">模型</div>
            <select v-model="agent.selectedModel" class="drawer-select" :disabled="agent.loading">
              <option v-for="m in SUPPORTED_MODELS" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>

          <!-- 会话工作目录 -->
          <div class="drawer-section">
            <div class="drawer-label">会话工作目录</div>
            <div class="ws-row">
              <button class="ws-pick-btn" title="选择目录" @click="showDirPicker = true">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
              </button>
              <input v-model="wsInput" class="ws-input" placeholder="F:\tetris" @keyup.enter="handleAddWorkspace" />
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
            <p v-if="agent.sessionDirectory" class="drawer-session-dir" :title="agent.sessionDirectory">当前会话：{{ agent.sessionDirectory }}</p>
          </div>

          <!-- 开关 -->
          <div class="drawer-section">
            <div class="drawer-label">选项</div>
            <div class="toggle-row">
              <span class="toggle-row-label">知识库检索</span>
              <label class="toggle">
                <input type="checkbox" v-model="agent.useVectorDb" :disabled="agent.loading" />
                <span class="toggle-slider"></span>
              </label>
            </div>
            <div class="toggle-row">
              <span class="toggle-row-label">自动朗读回复</span>
              <label class="toggle">
                <input type="checkbox" v-model="autoRead" :disabled="agent.loading" />
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>

          <!-- 工具 -->
          <div v-if="isWeatherEnabled" class="drawer-section">
            <div class="drawer-label">辅助工具</div>
            <button class="drawer-row-btn" @click="openWeather">
              <span class="drawer-row-icon">🌤️</span>
              <span class="drawer-row-text">天气预警</span>
              <span class="drawer-row-chevron">›</span>
            </button>
          </div>

          <!-- 危险操作 -->
          <div class="drawer-section">
            <button
              class="drawer-danger"
              :disabled="agent.loading || messages.length === 0"
              @click="handleClearConversation"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              清空当前对话
            </button>
          </div>
        </div>
      </aside>
    </transition>
    <WeatherAlert v-if="isWeatherEnabled" :show="showWeather" @update:show="showWeather = $event" />
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
        <div class="empty-orb">
          <div class="empty-orb-core">🤖</div>
          <div class="empty-orb-ring"></div>
        </div>
        <p class="empty-title">向所有智能体提问</p>
        <p class="empty-hint">多个 AI 智能体将并行处理你的请求</p>
      </div>

      <div v-else ref="parentRef" class="message-list" @scroll="onScroll">
        <div v-for="(msg, idx) in messages" :key="msg.id" class="message-wrapper">
          <div class="chat-message" :class="[msg.role, { 'is-error': msg.isError }]">
            <div class="avatar" :class="msg.role">
              <span v-if="msg.role === 'user'">👤</span>
              <span v-else-if="msg.isError">⚠️</span>
              <span v-else>🤖</span>
            </div>
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
                    <span v-if="copiedId === msg.id" class="copy-toast">已复制</span>
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

      <!-- ChatGPT 式「回到底部」 -->
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

    <!-- [F8] 聊天图片放大预览遮罩 -->
    <div v-if="previewImage" class="image-preview-overlay" @click.self="previewImage = ''">
      <img :src="previewImage" class="image-preview-img" alt="预览" @click="previewImage = ''" />
      <span class="image-preview-close" @click="previewImage = ''">✕</span>
    </div>
  </div>
</template>

<style scoped>
.multi-agent-view { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.chat-notice {
  margin: 0 24px 8px; padding: 8px 14px; border-radius: var(--radius);
  background: var(--warning-soft);
  color: var(--warning); font-size: 13px; flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--warning) 25%, transparent);
  animation: fadeSlideUp 0.3s var(--ease);
}

/* ── Header ── */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24px 32px 18px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 12px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--surface) 70%, transparent), transparent);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.chat-header h2 { margin: 0 0 3px; font-size: 22px; font-weight: 800; letter-spacing: -0.03em; }
.chat-header p { margin: 0; font-size: 13px; color: var(--text-secondary); }
.chat-header p.session-dir {
  margin-top: 6px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11.5px;
  color: var(--primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 460px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: var(--primary-glow);
  padding: 3px 10px;
  border-radius: var(--radius-pill);
}
@media (max-width: 768px) {
  .chat-header { padding: 16px; }
  .chat-header p.session-dir { max-width: 200px; }
  .message-list { padding: 16px; }
}

.header-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.status-badge { font-size: 12px; padding: 4px 10px; border-radius: var(--radius-pill); white-space: nowrap; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.status-badge.queued { background: var(--warning-soft); color: var(--warning); border: 1px solid color-mix(in srgb, var(--warning) 20%, transparent); }
.status-badge.running { background: var(--success-soft); color: var(--success); border: 1px solid color-mix(in srgb, var(--success) 20%, transparent); animation: pulse-stream 2s ease-in-out infinite; }
.pulse-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 0 0 color-mix(in srgb, var(--success) 50%, transparent);
  animation: dot-pulse 1.6s infinite;
}
@keyframes dot-pulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--success) 50%, transparent); }
  70% { box-shadow: 0 0 0 6px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}
@keyframes pulse-stream { 0%, 100% { opacity: 1; } 50% { opacity: 0.75; } }

.settings-toggle {
  width: 36px; height: 36px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: all var(--duration) var(--ease);
  opacity: 1;
}
.settings-toggle:hover, .settings-toggle.active {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-glow);
}

/* ── 设置抽屉 ── */
.settings-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(2, 6, 23, 0.45);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
  z-index: 1000;
}
.settings-drawer {
  position: fixed;
  top: 0;
  right: 0;
  height: 100%;
  width: 360px;
  max-width: 90vw;
  z-index: 1001;
  background: var(--surface-elevated);
  border-left: 1px solid var(--border);
  box-shadow: var(--shadow-2xl);
  display: flex;
  flex-direction: column;
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.drawer-title { font-size: 16px; font-weight: 800; letter-spacing: -0.02em; color: var(--text); }
.drawer-close {
  width: 32px; height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all var(--duration) var(--ease);
}
.drawer-close:hover { background: var(--bg-subtle); color: var(--text); }
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 22px 32px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}
.drawer-section { display: flex; flex-direction: column; gap: 8px; }
.drawer-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.drawer-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-subtle);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: border-color var(--duration) var(--ease);
}
.drawer-select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.drawer-select:disabled { opacity: 0.6; }
.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
}
.toggle-row-label { font-size: 13px; color: var(--text); font-weight: 500; }
.drawer-session-dir {
  font-size: 11.5px;
  color: var(--primary);
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drawer-row-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  background: var(--bg-subtle);
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration) var(--ease);
}
.drawer-row-btn:hover {
  border-color: var(--primary);
  background: var(--primary-glow);
}
.drawer-row-icon { font-size: 16px; flex-shrink: 0; }
.drawer-row-text { flex: 1; text-align: left; }
.drawer-row-chevron { color: var(--text-muted); font-size: 18px; line-height: 1; }
.drawer-danger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 11px 14px;
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
  border-radius: var(--radius);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--duration) var(--ease);
}
.drawer-danger:hover:not(:disabled) { background: color-mix(in srgb, var(--danger) 14%, var(--surface)); }
.drawer-danger:disabled { opacity: 0.4; cursor: not-allowed; }
.drawer-fade-enter-active, .drawer-fade-leave-active { transition: opacity 0.2s var(--ease); }
.drawer-fade-enter-from, .drawer-fade-leave-to { opacity: 0; }
.drawer-slide-enter-active, .drawer-slide-leave-active { transition: transform 0.25s var(--ease); }
.drawer-slide-enter-from, .drawer-slide-leave-to { transform: translateX(100%); }
@media (max-width: 520px) {
  .status-badge { display: none; }
}

/* 天气入口已收纳到设置抽屉，隐藏组件内原来的独立触发器按钮 */
:deep(.weather-alert-container .weather-toggle) { display: none !important; }

.toggle {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  user-select: none;
  font-weight: 500;
}
.toggle input { display: none; }
.toggle-slider {
  width: 32px; height: 18px;
  background: var(--border);
  border-radius: 10px;
  position: relative;
  transition: background 0.2s var(--ease);
  flex-shrink: 0;
}
.toggle-slider::after {
  content: '';
  position: absolute;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: #fff;
  top: 2px; left: 2px;
  transition: transform 0.2s var(--ease-spring);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}
.toggle input:checked + .toggle-slider { background: linear-gradient(135deg, var(--primary), var(--accent)); }
.toggle input:checked + .toggle-slider::after { transform: translateX(14px); }
.toggle input:disabled + .toggle-slider { opacity: 0.5; }
.clear-chat-btn { flex-shrink: 0; }
.clear-chat-btn:hover:not(:disabled) { color: var(--danger); background: var(--danger-soft); opacity: 1; }
.clear-chat-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.speak-btn.speaking { color: var(--primary); opacity: 1; animation: speak-pulse 1.2s ease-in-out infinite; }
@keyframes speak-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }

/* ── Workspace Manager ── */
.ws-manager { position: relative; flex-shrink: 0; }
.ws-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--duration) var(--ease);
}
.ws-btn:hover { border-color: var(--primary); color: var(--primary); }
.ws-panel {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 8px;
  width: 380px;
  max-width: 75vw;
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  padding: 14px;
  z-index: 50;
  animation: scaleIn 0.2s var(--ease-spring);
}
.ws-row { display: flex; gap: 8px; }
.ws-pick-btn {
  width: 34px; height: 34px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-subtle);
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
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--bg-subtle);
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.ws-input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.ws-add {
  padding: 7px 14px;
  border: none;
  border-radius: var(--radius);
  background: linear-gradient(135deg, var(--primary), color-mix(in srgb, var(--primary) 80%, var(--accent)));
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.ws-add:disabled { opacity: 0.5; cursor: not-allowed; }
.ws-error { color: var(--danger); font-size: 12px; margin: 8px 0 0; }
.ws-list {
  margin-top: 10px;
  max-height: 210px;
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
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}
.ws-item.fixed { opacity: 0.7; }
.ws-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); flex-shrink: 0; }
.ws-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px; }
.ws-tag { font-size: 10px; padding: 1px 6px; border-radius: 4px; background: var(--primary-glow); color: var(--primary); flex-shrink: 0; font-weight: 600; }
.ws-remove { border: none; background: transparent; color: var(--text-muted); font-size: 16px; line-height: 1; cursor: pointer; padding: 0 2px; flex-shrink: 0; }
.ws-remove:hover { color: var(--danger); }
.ws-empty { font-size: 12px; color: var(--text-muted); margin: 0; padding: 4px; }

/* ── Chat Body ── */
.chat-body { position: relative; flex: 1; overflow: hidden; display: flex; flex-direction: column; }

/* Empty State */
.empty-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--text-secondary); animation: fadeSlideUp 0.5s var(--ease); }
.empty-state .empty-orb { position: relative; width: 88px; height: 88px; margin-bottom: 12px; }
.empty-orb-core {
  position: absolute; inset: 16px;
  border-radius: 50%;
  background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 20%, var(--surface)), color-mix(in srgb, var(--accent) 12%, var(--surface)));
  display: flex; align-items: center; justify-content: center;
  font-size: 30px;
  box-shadow: 0 0 30px var(--primary-glow), 0 8px 24px rgba(0,0,0,0.1);
  animation: float-orb 4s ease-in-out infinite;
}
.empty-orb-ring {
  position: absolute; inset: 0;
  border-radius: 50%;
  border: 2px dashed color-mix(in srgb, var(--primary) 35%, transparent);
  animation: spin 24s linear infinite;
}
@keyframes float-orb {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}
.empty-title { font-size: 16px; font-weight: 700; color: var(--text); }
.empty-hint { font-size: 13px; margin-top: 2px; }

/* Message List */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  scroll-behavior: smooth;
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}
.message-list::-webkit-scrollbar { width: 6px; }
.message-list::-webkit-scrollbar-track { background: transparent; }
.message-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Footer */
.chat-footer { flex-shrink: 0; }

/* Messages */
.chat-message { display: flex; gap: 12px; margin-bottom: 24px; animation: fadeSlideUp 0.35s var(--ease); }
.chat-message.user { flex-direction: row-reverse; }

.avatar {
  width: 38px; height: 38px;
  border-radius: 12px;
  background: var(--bg-subtle);
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}
.avatar.user {
  background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 18%, var(--surface)), color-mix(in srgb, var(--accent) 10%, var(--surface)));
  border-color: color-mix(in srgb, var(--primary) 25%, transparent);
}

.bubble {
  flex: 1; width: 100%; max-width: 100%;
  padding: 14px 18px;
  border-radius: var(--radius-lg);
  background: var(--surface);
  border: 1px solid var(--border);
  line-height: 1.75;
  font-size: 14.5px;
  box-sizing: border-box;
  border-top-left-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
}
.user .bubble {
  flex: none; width: auto; max-width: 80%;
  background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 12%, var(--surface)), color-mix(in srgb, var(--accent) 6%, var(--surface)));
  color: var(--text);
  border-color: color-mix(in srgb, var(--primary) 20%, var(--border));
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-sm);
}
.content { white-space: pre-wrap; word-break: break-word; }

/* [F8] 用户消息附件回显 */
.msg-files { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.msg-file-image { max-width: 200px; max-height: 200px; border-radius: var(--radius); object-fit: cover; display: block; cursor: zoom-in; box-shadow: var(--shadow-sm); }
.msg-file-caption { display: block; font-size: 12px; color: var(--text-secondary); margin-top: 4px; max-width: 200px; }
.msg-file-name { font-size: 12px; padding: 4px 10px; border-radius: var(--radius-pill); background: var(--bg-subtle); display: inline-block; border: 1px solid var(--border); }

.is-error .bubble { background: var(--danger-soft); border-color: color-mix(in srgb, var(--danger) 30%, var(--border)); }

.message-footer { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border-subtle); font-size: 11px; }
.user .message-footer { border-top-color: color-mix(in srgb, var(--primary) 15%, var(--border)); }
.time { color: var(--text-muted); opacity: 0.85; font-variant-numeric: tabular-nums; }
.message-actions { display: flex; gap: 4px; align-items: center; }
.btn-wrapper { position: relative; display: inline-flex; }
.copy-toast {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 6px);
  transform: translateX(-50%);
  white-space: nowrap;
  font-size: 11px;
  background: var(--text);
  color: var(--bg);
  padding: 3px 10px;
  border-radius: var(--radius-pill);
  pointer-events: none;
  animation: fadeInOut 1.5s ease-in-out;
  box-shadow: var(--shadow-md);
}
@keyframes fadeInOut {
  0% { opacity: 0; transform: translateX(-50%) translateY(4px); }
  15% { opacity: 1; transform: translateX(-50%) translateY(0); }
  85% { opacity: 1; transform: translateX(-50%) translateY(0); }
  100% { opacity: 0; transform: translateX(-50%) translateY(-4px); }
}
.icon-btn {
  width: 30px; height: 30px;
  border-radius: var(--radius-sm);
  border: none;
  background: transparent;
  color: inherit;
  opacity: 0.5;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--duration) var(--ease);
}
.icon-btn:hover { opacity: 1; background: var(--bg-subtle); }
.user .icon-btn:hover { background: color-mix(in srgb, var(--primary) 12%, transparent); }
.retry-countdown { border: none; background: transparent; color: var(--warning); font-size: 12px; font-variant-numeric: tabular-nums; cursor: default; padding: 0 4px; }
.delete-btn:hover { color: var(--danger) !important; background: var(--danger-soft) !important; }
.btn-danger { background: var(--danger); color: #fff; border: none; border-radius: var(--radius); padding: 6px 12px; font-size: 13px; font-weight: 600; cursor: pointer; }
.btn-danger:hover { background: color-mix(in srgb, var(--danger) 85%, #000); }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }

  /* @@CHAT_SCROLL_INJECTED@@ */
  /* ── ChatGPT 式「回到底部」浮动按钮（用户滚动优先） ── */
  .scroll-to-bottom-btn {
    position: absolute;
    right: 24px;
    bottom: 20px;
    width: 46px;
    height: 46px;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: var(--surface-glass);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: var(--shadow-lg);
    transition: all 0.2s var(--ease);
    z-index: 20;
    -webkit-tap-highlight-color: transparent;
  }
  .scroll-to-bottom-btn:hover {
    color: var(--primary);
    transform: translateY(-2px);
    box-shadow: var(--shadow-xl);
    border-color: var(--primary);
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
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
  }
  .image-preview-img {
    max-width: 92vw;
    max-height: 88vh;
    object-fit: contain;
    border-radius: var(--radius-lg);
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5);
    animation: scaleIn 0.2s var(--ease);
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
    transition: all 0.15s;
  }
  .image-preview-close:hover { background: rgba(255, 255, 255, 0.25); }
</style>
