<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import type { FileContent, VoiceMessageData, MultiAgentMessage } from '../types'
import { useAuthStore } from '../stores/auth'
  import { useChatSettingsStore } from '../stores/chatSettings'
import { synthesize, speakNative, stopNative } from '../api/voice'
import MultiAgentResponse from '../components/MultiAgentResponse.vue'
import ChatInput from '../components/ChatInput.vue'
import VoiceBubble from '../components/VoiceBubble.vue'

const route = useRoute()
const router = useRouter()
const agent = useMultiAgentStore()
const auth = useAuthStore()
const parentRef = ref<HTMLElement>()
const chatInputRef = ref<any>()
const isNearBottom = ref(true)

function isImgAvatar(v: string): boolean {
  return !!v && (v.startsWith('data:') || v.startsWith('http'))
}
// [F8] 聊天图片点击放大预览（当前预览图的 data URL；空串 = 未预览）
const previewImage = ref('')

const messages = computed(() => agent.messages)

// ── ChatGPT 式滚动交互 ──
const showScrollBtn = computed(() => !isNearBottom.value && messages.value.length > 0)

function scrollToBottom(behavior: ScrollBehavior = 'smooth') {
  const el = parentRef.value
  if (!el) return
  isNearBottom.value = true
  el.scrollTo({ top: el.scrollHeight, behavior })
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

// ── [模型目录] 消息头 usage 摘要（model · in→out · cost；hover 看明细）──
function fmtTokens(n?: number) {
  if (!n) return ''
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : `${n}`
}
function fmtCost(n?: number) {
  if (!n || n <= 0) return ''
  return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`
}
function usageLabel(msg: MultiAgentMessage): string {
  const t = msg.tokens || {}
  const parts: string[] = []
  if (msg.model) parts.push(msg.model)
  if (t.input || t.output) parts.push(`${fmtTokens(t.input)}→${fmtTokens(t.output)}`)
  const c = fmtCost(msg.cost)
  if (c) parts.push(c)
  return parts.join(' · ')
}
function usageDetail(msg: MultiAgentMessage): string {
  const t = msg.tokens || {}
  const lines = [`模型: ${msg.model || '-'}`]
  if (t.input != null || t.output != null || t.reasoning != null || t.cache_read != null || t.cache_write != null) {
    lines.push(`输入 ${fmtTokens(t.input)} · 输出 ${fmtTokens(t.output)} · 推理 ${fmtTokens(t.reasoning)} · 缓存读 ${fmtTokens(t.cache_read)} · 缓存写 ${fmtTokens(t.cache_write)}`)
  }
  lines.push(`成本: ${fmtCost(msg.cost) || '$0.00'}`)
  return lines.join('\n')
}

onMounted(() => {
  const id = route.params.id as string
  if (id) agent.loadConversation(id)
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
    audio.onerror = () => { stopSpeaking(); speakNative((content || '').trim(), chatSettings.ttsLang) }
    await audio.play()
  } catch {
    // [fix] 后端 qwen TTS 失败降级时也要带上「朗读语言」，而非固定中文
    speakNative((content || '').trim(), chatSettings.ttsLang)
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
        <h2>AI 智能助手</h2>
        <p>统一 Agent：知识库 + 代码/文件 + 联网搜索（输入框可选 规划/探索 模式）</p>
      </div>
      <div class="header-actions">
        <div v-if="agent.queuePosition != null" class="status-badge queued">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          排队 #{{ agent.queuePosition }}
        </div>
        <div v-else-if="agent.loading" class="status-badge running">
          <span class="pulse-dot"></span> 运行中
        </div>
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
        <div class="empty-orb">
          <div class="empty-orb-core">🤖</div>
          <div class="empty-orb-ring"></div>
        </div>
        <p class="empty-title">向智能助手提问</p>
        <p class="empty-hint">知识库检索、代码编写、联网搜索一站完成</p>
      </div>

      <div v-else ref="parentRef" class="message-list" @scroll="onScroll">
        <div v-for="(msg, idx) in messages" :key="msg.id" class="message-wrapper">
          <div class="chat-message" :class="[msg.role, { 'is-error': msg.isError }]">
            <div class="avatar" :class="msg.role">
              <template v-if="msg.role === 'user'">
                <img v-if="isImgAvatar(auth.avatar)" class="avatar-img" :src="auth.avatar" alt="avatar" />
                <span v-else-if="auth.avatar">{{ auth.avatar }}</span>
                <span v-else>👤</span>
              </template>
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
                <span
                  v-if="msg.role !== 'user' && (msg.model || (msg.tokens && (msg.tokens.input || msg.tokens.output)))"
                  class="msg-usage"
                  :title="usageDetail(msg)"
                >{{ usageLabel(msg) }}</span>
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

    <!-- [F8] 聊天图片放大预览遮罩 -->
    <div v-if="previewImage" class="image-preview-overlay" @click.self="previewImage = ''">
      <img :src="previewImage" class="image-preview-img" alt="预览" @click="previewImage = ''" />
      <span class="image-preview-close" @click="previewImage = ''">✕</span>
    </div>
  </div>
</template>


<style scoped src="../styles/chat/multiAgentView.css"></style>
