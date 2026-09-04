<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import { SUPPORTED_MODELS } from '../config/models'
import type { FileContent, VoiceMessageData } from '../types'
import { usePermissionStore } from '../stores/permission'
import { useThemeStore, BG_VARIANTS } from '../stores/theme'
  import { useChatSettingsStore, TTS_LANGUAGES } from '../stores/chatSettings'
import { synthesize, speakNative, stopNative } from '../api/voice'
import MultiAgentResponse from '../components/MultiAgentResponse.vue'
import ChatInput from '../components/ChatInput.vue'
import WeatherAlert from '../components/WeatherAlert.vue'
import DirPickerModal from '../components/DirPickerModal.vue'
import VoiceBubble from '../components/VoiceBubble.vue'

const route = useRoute()
const router = useRouter()
const agent = useMultiAgentStore()
const perm = usePermissionStore()
const theme = useThemeStore()
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
  window.addEventListener('keydown', onGlobalKeydown)
})

// ── 双击 Esc 快捷取消：连按两次 Esc → 取消当前任务并清空队列 ──
let escCount = 0
let escTimer: number | null = null
function onGlobalKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape') { escCount = 0; return }
  const now = Date.now()
  // 1s 外的第一次 Esc 视为新的一次
  if (escCount === 0) {
    if (escTimer) { clearTimeout(escTimer); escTimer = null }
    escTimer = window.setTimeout(() => { escCount = 0 }, 1000)
  }
  escCount += 1
  if (escCount === 2) {
    escCount = 0
    if (escTimer) { clearTimeout(escTimer); escTimer = null }
    if (agent.loading || pendingQueue.value.length) {
      handleCancel()
    }
  }
}

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

function handleSend(text: string, files?: FileContent[], voice?: VoiceMessageData) {
  markPendingAutoRead()
  // 任务正在进行时入队，完成后自动发出（排队方案）
  if (agent.loading) {
    pendingQueue.value.push({ text, files: files || [], voice })
    return
  }
  agent.send(text, undefined, files || [], voice).then((completed) => {
    if (completed && agent.conversationId && route.name !== 'MultiAgentConversation') {
      router.push({ name: 'MultiAgentConversation', params: { id: agent.conversationId } })
    }
  })
}

// ── 待发消息队列：agent 运行中发送的内容先进队，done 后自动发出下一条 ──
type QueuedMsg = { text: string; files: FileContent[]; voice?: VoiceMessageData }
const pendingQueue = ref<QueuedMsg[]>([])
const queueCount = computed(() => pendingQueue.value.length)
watch(() => agent.loading, (loading) => {
  // 空闲后自动发队列中的下一条
  if (!loading && pendingQueue.value.length) {
    const next = pendingQueue.value.shift()!
    agent.send(next.text, undefined, next.files, next.voice).then((completed) => {
      if (completed && agent.conversationId && route.name !== 'MultiAgentConversation') {
        router.push({ name: 'MultiAgentConversation', params: { id: agent.conversationId } })
      }
    })
  }
})
function cancelQueue() {
  pendingQueue.value = []
}

function handleCancel() { cancelQueue(); agent.cancel() }

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

const chatSettings = useChatSettingsStore()
const autoRead = computed(() => chatSettings.autoRead)
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
    const url = await synthesize(text, undefined, chatSettings.ttsLang)
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
onBeforeUnmount(() => {
  stopSpeaking()
  window.removeEventListener('keydown', onGlobalKeydown)
  if (escTimer) clearTimeout(escTimer)
})

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
          <!-- 外观 / 背景色 -->
          <div class="drawer-section">
            <div class="drawer-label">背景颜色</div>
            <div class="bg-picker">
              <button
                v-for="v in BG_VARIANTS"
                :key="v.value"
                class="bg-swatch"
                :title="v.label"
                :class="{ active: theme.bgVariant === v.value }"
                :style="{ '--sw': v.swatch, '--sw-glow': v.glowSwatch || v.swatch }"
                @click="theme.setBg(v.value)"
              >
                <span class="bg-swatch-dot"></span>
                <span class="bg-swatch-label">{{ v.label }}</span>
              </button>
            </div>
          </div>

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
                <input type="checkbox" :checked="autoRead" :disabled="agent.loading" @change="chatSettings.autoRead = ($event.target as HTMLInputElement).checked" />
                <span class="toggle-slider"></span>
              </label>
            </div>
            <div class="toggle-row">
              <span class="toggle-row-label">朗读语言</span>
              <select v-model="chatSettings.ttsLang" class="drawer-select tts-lang-select" :disabled="agent.loading">
                <option v-for="l in TTS_LANGUAGES" :key="l.value" :value="l.value">{{ l.label }}</option>
              </select>
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
                <!-- [语音消息] 微信式音频气泡 -->
                <VoiceBubble v-if="msg.voice" :voice="msg.voice" />
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

    <!-- 待发消息队列提示条 -->
    <div v-if="queueCount" class="send-queue-bar">
      <span class="send-queue-text">
        ⏳ 当前任务进行中，已加入队列：{{ queueCount }} 条待发，完成后自动依次发送（按两次 Esc 可取消）
      </span>
      <button class="send-queue-clear" @click="cancelQueue">取消全部</button>
    </div>

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


<style scoped src="../styles/chat/multiAgentView.css"></style>
