<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  cancelInbox,
  commitRevert,
  compactSession,
  createSession,
  fetchTurnDiff,
  forkSession,
  interruptSession,
  listAgents,
  replyForm,
  replyPermission,
  restoreTurnSnapshot,
  stageRevert,
  switchSessionAgent,
  switchSessionModel,
  updateInboxDelivery,
} from '../api/v2'
import type { AgentInfoV2, TurnDiff } from '../api/v2'
import { listCommands, type SlashCommandInfo } from '../api/commands'
import { listReferences, type ReferenceItem } from '../api/references'
import type {
  AssistantMessage,
  FileAttachment,
  FormInfo,
  InboxInfo,
  MessagePart,
  MessageV2,
  PermissionRequest,
  ReasoningPart,
  TextPart,
  VoiceAttachment,
} from '../contracts/v2'
import { uploadVoiceMessage } from '../api/voice'
import { PressToTalkRecorder } from '../voice/recorder'
import { useModelManagerStore } from '../stores/modelManager'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useAuthStore } from '../stores/auth'
import { useSkillStore } from '../stores/skills'
import { useV2SessionStore } from '../v2/sessionStore'


const route = useRoute()
const router = useRouter()
const v2 = useV2SessionStore()
const models = useModelManagerStore()
const legacy = useMultiAgentStore()
const auth = useAuthStore()
const skillStore = useSkillStore()

const draft = ref('')
const sending = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const pendingFiles = ref<FileAttachment[]>([])
const pendingVoice = ref<VoiceAttachment | null>(null)
const recording = ref(false)
const recordSeconds = ref(0)
const voiceBusy = ref(false)
const recorder = new PressToTalkRecorder()
const scroller = ref<HTMLElement | null>(null)
const formAnswers = ref<Record<string, Record<string, string>>>({})
const activeDiff = ref<{ messageId: string; diff: TurnDiff } | null>(null)
const diffLoading = ref(false)
const copiedId = ref('')
const composerRef = ref<HTMLTextAreaElement | null>(null)
const slashDismissed = ref(false)
const slashActive = ref(0)
const slashSearch = ref('')
const slashListRef = ref<HTMLElement | null>(null)
const slashSearchRef = ref<HTMLInputElement | null>(null)

const sessionId = computed(() => String(route.params.id || ''))
const runtime = computed(() => (sessionId.value ? v2.getRuntime(sessionId.value) : undefined))
const messages = computed(() => runtime.value?.messages ?? [])
const inbox = computed(() => runtime.value?.inbox ?? [])
const permissions = computed(() => runtime.value?.permissions ?? [])
const forms = computed(() => runtime.value?.forms ?? [])
const status = computed(() => runtime.value?.status?.type ?? 'idle')
const executionError = computed(() => runtime.value?.error?.message || '')
const modelId = computed(() => runtime.value?.info?.model?.id || '')
const pendingModel = ref('')
const effectiveModelId = computed(
  () => modelId.value
    || pendingModel.value
    || String(models.config?.default_model || '')
    || models.modelOptions[0]?.value
    || '',
)
const currentModel = computed(
  () => models.modelOptions.find(model => model.value === effectiveModelId.value)
    || models.modelOptions[0],
)
const currentModelLabel = computed(() => currentModel.value?.label || '默认模型')
const currentModelDesc = computed(() => currentModel.value?.desc || '')
const modelMenuOpen = ref(false)
const modelTipVisible = ref(false)

const pendingAgent = ref('')
const agentOptions = ref<AgentInfoV2[]>([])
const agentMenuOpen = ref(false)
const agentTipVisible = ref(false)
const currentAgentName = computed(
  () => runtime.value?.info?.agent || pendingAgent.value || 'build',
)
const currentAgent = computed(
  () => agentOptions.value.find(agent => agent.name === currentAgentName.value) || null,
)
const currentAgentLabel = computed(() => currentAgent.value?.name || currentAgentName.value)
const currentAgentDesc = computed(
  () => currentAgent.value?.description || '默认 Agent',
)

async function ensureAgents() {
  try {
    const agents = await listAgents(sessionId.value || null)
    agentOptions.value = agents.filter(agent => !agent.disabled && !agent.hidden)
  } catch {
    agentOptions.value = []
  }
}

async function pickAgent(name: string) {
  agentMenuOpen.value = false
  if (!name || name === currentAgentName.value) return
  const id = sessionId.value
  if (!id) {
    pendingAgent.value = name
    return
  }
  try {
    const info = await switchSessionAgent(id, name)
    v2.setInfo(id, info)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}
const referenceItems = ref<ReferenceItem[]>([])
const referenceMenuOpen = ref(false)

async function ensureReferences() {
  try {
    referenceItems.value = (await listReferences(sessionId.value || null)).filter(
      (item) => !item.hidden,
    )
  } catch {
    referenceItems.value = []
  }
}

function useReference(name: string) {
  referenceMenuOpen.value = false
  const prefix = draft.value && !draft.value.endsWith(' ') ? ' ' : ''
  draft.value = `${draft.value}${prefix}@ref:${name} `
  void nextTick(() => composerRef.value?.focus())
}
const userInitial = computed(() => (auth.displayName || 'U').slice(0, 1).toUpperCase())

function hasImageAvatar(): boolean {
  return !!auth.avatar && (auth.avatar.startsWith('data:') || auth.avatar.startsWith('http'))
}
const canSend = computed(
  () =>
    (draft.value.trim().length > 0 || pendingFiles.value.length > 0 || pendingVoice.value !== null) &&
    !sending.value,
)

function scrollToBottom() {
  void nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

watch(() => [messages.value.length, status.value], scrollToBottom)

// ── `/skills` 技能选择器 ──────────────────────────────────────────
// 在行首或空白之后输入 `/skills` 弹出技能/命令；选中后把 `/技能名 ` 写回输入框，
// 发送时后端 `apply_slash_skill` 会加载该技能正文并注入模型上下文。
const slashToken = computed(() => {
  const match = /(^|\s)(\/\S*)$/.exec(draft.value)
  if (!match) return null
  return { start: draft.value.length - match[2].length, token: match[2].toLowerCase() }
})
const slashTrigger = computed(
  () => (slashToken.value?.token === '/skills' ? slashToken.value.start : -1),
)
const slashRest = computed(() => {
  const trigger = slashToken.value
  return trigger ? draft.value.slice(trigger.start + trigger.token.length) : ''
})
interface SlashItem {
  name: string
  description: string
  kind: 'skill' | 'command'
}
const commandItems = ref<SlashCommandInfo[]>([])
const commandsLoading = ref(false)
const slashAll = computed<SlashItem[]>(() => [
  ...skillStore.skills
    .filter((skill) => skill.enabled)
    .map((skill) => ({
      name: skill.name,
      description: skill.description || '',
      kind: 'skill' as const,
    })),
  ...commandItems.value.map((command) => ({
    name: command.name,
    description: command.description || (command.agent ? `Agent: ${command.agent}` : ''),
    kind: 'command' as const,
  })),
])
async function ensureCommands() {
  if (commandsLoading.value) return
  commandsLoading.value = true
  try {
    commandItems.value = await listCommands(sessionId.value || null)
  } catch {
    commandItems.value = []
  } finally {
    commandsLoading.value = false
  }
}
const slashOpen = computed(() => !slashDismissed.value && slashTrigger.value >= 0)
const slashQuery = computed(() => {
  const typed = slashSearch.value.trim()
  if (typed) return typed.toLowerCase()
  return slashRest.value.trim().toLowerCase()
})
const slashFiltered = computed(() => {
  const query = slashQuery.value
  const available = slashAll.value
  if (!query) return available
  return available.filter(
    (item) =>
      item.name.toLowerCase().includes(query)
      || item.description.toLowerCase().includes(query),
  )
})

watch(slashOpen, (open) => {
  if (!open) return
  slashActive.value = 0
  slashSearch.value = ''
  if (!skillStore.initialized) void skillStore.fetchAll()
  void ensureCommands()
  void nextTick(() => {
    if (slashListRef.value) slashListRef.value.scrollTop = 0
    slashSearchRef.value?.focus()
  })
})
watch(slashFiltered, () => {
  slashActive.value = 0
})
watch(draft, () => {
  slashDismissed.value = false
})

function ensureSlashActiveVisible() {
  const list = slashListRef.value
  if (!list) return
  const el = list.children[slashActive.value] as HTMLElement | undefined
  if (!el) return
  const listRect = list.getBoundingClientRect()
  const elRect = el.getBoundingClientRect()
  if (elRect.top < listRect.top) list.scrollTop -= listRect.top - elRect.top
  else if (elRect.bottom > listRect.bottom) list.scrollTop += elRect.bottom - listRect.bottom
}

function slashMove(step: number) {
  const count = slashFiltered.value.length
  if (!count) return
  slashActive.value = (slashActive.value + step + count) % count
  void nextTick(ensureSlashActiveVisible)
}

function closeSlash() {
  slashDismissed.value = true
  slashSearch.value = ''
  void nextTick(() => composerRef.value?.focus())
}

function slashUse(item: SlashItem) {
  const i = slashTrigger.value
  const prefix = i >= 0 ? draft.value.slice(0, i) : ''
  draft.value = `${prefix}/${item.name} `
  slashDismissed.value = true
  slashSearch.value = ''
  void nextTick(() => {
    composerRef.value?.focus()
    scrollToBottom()
  })
}

function onSlashSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    slashMove(1)
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    slashMove(-1)
    return
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    closeSlash()
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const item = slashFiltered.value[slashActive.value]
    if (item) slashUse(item)
    return
  }
}

function onComposerKeydown(event: KeyboardEvent) {
  if (slashOpen.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      slashMove(1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      slashMove(-1)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeSlash()
      return
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      const item = slashFiltered.value[slashActive.value]
      if (item) {
        event.preventDefault()
        slashUse(item)
        return
      }
    }
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

async function loadSession() {
  const id = sessionId.value
  if (!id) return
  await v2.hydrate(id)
  v2.setActive(id)
}

onMounted(() => {
  v2.start()
  void models.load()
  void ensureReferences()
  void ensureAgents()
  void loadSession()
})

onBeforeUnmount(() => {
  // One global stream stays alive across tabs; only clear view ownership.
  recorder.cancelNow()
  v2.setActive(null)
})

watch(() => route.params.id, () => {
  error.value = ''
  void ensureAgents()
  void loadSession()
})

async function send() {
  const text = draft.value.trim()
  if ((!text && !pendingFiles.value.length && !pendingVoice.value) || sending.value) return
  sending.value = true
  error.value = ''
  try {
    let id = sessionId.value
    if (!id) {
      const info = await createSession({
        title: (text || '附件').slice(0, 24),
        directory: legacy.activeDirectory || undefined,
        agent: pendingAgent.value || undefined,
        model: pendingModel.value
          ? { id: pendingModel.value, provider_id: '', variant: null }
          : null,
      })
      id = info.id
      pendingModel.value = ''
      pendingAgent.value = ''
      v2.setInfo(id, info)
      void legacy.loadConversations()
      const name = route.name === 'MultiAgent' ? 'MultiAgentConversation' : 'V2ChatConversation'
      await router.replace({ name, params: { id } })
    }
    const files = pendingFiles.value.slice()
    const voice = pendingVoice.value
    await v2.send(id, { text: text || voice?.text || '请查看附件', files, voice, resume: true })
    draft.value = ''
    pendingFiles.value = []
    pendingVoice.value = null
    scrollToBottom()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    sending.value = false
  }
}

function chooseFiles() {
  fileInput.value?.click()
}

function readFileData(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const value = String(reader.result || '')
      resolve(value.includes(',') ? value.slice(value.indexOf(',') + 1) : value)
    }
    reader.onerror = () => reject(reader.error || new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files || [])
  input.value = ''
  const next = pendingFiles.value.slice()
  for (const file of selected) {
    if (file.size > 20 * 1024 * 1024) {
      error.value = `${file.name} 超过 20MB`
      continue
    }
    try {
      next.push({
        type: 'file',
        uri: file.name,
        name: file.name,
        mime: file.type || 'application/octet-stream',
        data: await readFileData(file),
      })
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    }
  }
  pendingFiles.value = next
}

function removeFile(index: number) {
  pendingFiles.value = pendingFiles.value.filter((_, i) => i !== index)
}

async function toggleVoice() {
  if (recording.value) {
    await stopVoice()
    return
  }
  try {
    await recorder.start((frame) => {
      recordSeconds.value = frame.seconds
    })
    recording.value = true
    recordSeconds.value = 0
  } catch (err) {
    error.value = err instanceof Error ? err.message : '麦克风不可用'
  }
}

async function stopVoice() {
  const result = await recorder.stop()
  recording.value = false
  if (!result) return
  if (result.duration < 1) {
    error.value = '说话时间太短'
    return
  }
  voiceBusy.value = true
  try {
    const up = await uploadVoiceMessage(
      result.blob,
      result.duration,
      result.peaks,
      '',
      'voice.webm',
    )
    pendingVoice.value = {
      id: up.id,
      url: up.url,
      duration: up.duration,
      waveform: up.waveform,
      text: up.text || '',
    }
    if (up.text && !draft.value.trim()) draft.value = up.text
  } catch (err) {
    error.value = err instanceof Error ? err.message : '语音上传失败'
  } finally {
    voiceBusy.value = false
  }
}

async function stop() {
  const id = sessionId.value
  if (!id) return
  try {
    await interruptSession(id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function compact() {
  const id = sessionId.value
  if (!id) return
  try {
    await compactSession(id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function fork() {
  const id = sessionId.value
  if (!id) return
  try {
    const info = await forkSession(id)
    v2.setInfo(info.id, info)
    const name = route.name === 'V2ChatConversation' ? 'V2ChatConversation' : 'MultiAgentConversation'
    await router.push({ name, params: { id: info.id } })
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function revertTo(messageId: string) {
  const id = sessionId.value
  if (!id) return
  try {
    await stageRevert(id, messageId)
    await commitRevert(id)
    await v2.hydrate(id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function showDiff(messageId: string) {
  const id = sessionId.value
  if (!id) return
  if (activeDiff.value?.messageId === messageId) {
    activeDiff.value = null
    return
  }
  diffLoading.value = true
  try {
    const diff = await fetchTurnDiff(id, messageId)
    activeDiff.value = { messageId, diff }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    diffLoading.value = false
  }
}

async function restoreFiles(messageId: string) {
  const id = sessionId.value
  if (!id) return
  try {
    const result = await restoreTurnSnapshot(id, messageId)
    if (!result.restored) {
      error.value = result.restored_files.length ? '部分文件未恢复' : '该轮没有可恢复的文件改动'
      return
    }
    activeDiff.value = null
    await v2.hydrate(id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function messageText(message: MessageV2): string {
  if (message.type === 'user') return message.text || ''
  if (message.type === 'assistant') {
    return message.content
      .filter((part): part is Extract<MessagePart, { type: 'text' }> => part.type === 'text')
      .map((part) => part.text)
      .join('\n\n')
  }
  return ''
}

async function writeClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    document.execCommand('copy')
  } finally {
    document.body.removeChild(area)
  }
}

async function copyMessage(message: MessageV2) {
  const text = messageText(message)
  if (!text) return
  try {
    await writeClipboard(text)
    copiedId.value = message.id
    window.setTimeout(() => {
      if (copiedId.value === message.id) copiedId.value = ''
    }, 1500)
  } catch {
    error.value = '复制失败'
  }
}

async function answerPermission(request: PermissionRequest, decision: 'once' | 'always' | 'reject') {
  const id = sessionId.value
  if (!id) return
  try {
    await replyPermission(id, request.id, decision)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function fieldValue(form: FormInfo, fieldId: string): string {
  return formAnswers.value[form.id]?.[fieldId] ?? ''
}

function setFieldValue(form: FormInfo, fieldId: string, value: string) {
  const answers = { ...(formAnswers.value[form.id] || {}), [fieldId]: value }
  formAnswers.value = { ...formAnswers.value, [form.id]: answers }
}

async function submitForm(form: FormInfo) {
  const id = sessionId.value
  if (!id) return
  try {
    await replyForm(id, form.id, formAnswers.value[form.id] || {})
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function changeInbox(item: InboxInfo, delivery: 'steer' | 'queue') {
  const id = sessionId.value
  if (!id) return
  try {
    await updateInboxDelivery(id, item.id, delivery)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function removeInbox(item: InboxInfo) {
  const id = sessionId.value
  if (!id) return
  try {
    await cancelInbox(id, item.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function pickModel(value: string) {
  modelMenuOpen.value = false
  if (!value) return
  const id = sessionId.value
  if (!id) {
    pendingModel.value = value
    return
  }
  if (value === effectiveModelId.value) return
  try {
    const info = await switchSessionModel(id, { id: value, provider_id: '', variant: null })
    v2.setInfo(id, info)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}


function assistant(message: MessageV2): AssistantMessage | null {
  return message.type === 'assistant' ? message : null
}

function textParts(message: MessageV2): MessagePart[] {
  const value = assistant(message)
  return value ? value.content : []
}

/** 最终答案只渲染正文；工具、推理、压缩等只在执行中展示。 */
function answerParts(message: MessageV2): TextPart[] {
  return textParts(message).filter((part): part is TextPart => part.type === 'text')
}

const executing = computed(() => status.value === 'busy' || status.value === 'retry')
const activeAssistantId = computed(
  () => (executing.value ? runtime.value?.activeAssistantId ?? null : null),
)
const activeAssistant = computed<AssistantMessage | null>(() => {
  const id = activeAssistantId.value
  if (!id) return null
  const message = messages.value.find((item) => item.id === id)
  return message?.type === 'assistant' ? message : null
})
const liveCompaction = computed(() => (executing.value ? runtime.value?.compaction ?? null : null))

function isActiveMessage(message: MessageV2): boolean {
  return message.type === 'assistant' && message.id === activeAssistantId.value
}

function execReasoning(message: MessageV2): ReasoningPart[] {
  return textParts(message).filter(
    (part): part is ReasoningPart => part.type === 'reasoning' && part.text.trim().length > 0,
  )
}

/** 回合进行中的模型输出属于执行信息；回合结束同一段文本才作为最终答案。 */
function outputParts(message: MessageV2): TextPart[] {
  return answerParts(message).filter((part) => part.text.trim().length > 0)
}

function hasExecInfo(message: MessageV2): boolean {
  return (
    execReasoning(message).length > 0
    || outputParts(message).length > 0
    || liveCompaction.value !== null
  )
}

/**
 * 一轮工具调用在 DB 里会投影成多条 assistant（每个 AI tool_calls 一条 + 最终答案一条）。
 * 时间线只保留：进行中的那条，以及不含工具调用且有正文的最终答案；其余隐藏，
 * 避免出现「一条空回答 / 中间旁白被当成回答」。
 */
const renderedMessages = computed(() =>
  messages.value.filter((message) => {
    if (message.type === 'compaction') return false
    if (message.type !== 'assistant') return true
    if (isActiveMessage(message)) return true
    const parts = textParts(message)
    if (parts.some((part) => part.type === 'tool')) return false
    return parts.some((part) => part.type === 'text' && part.text.trim().length > 0)
  }),
)

const compactionStateText = computed(() => {
  const value = liveCompaction.value
  if (!value) return ''
  if (value.status === 'running') return value.reason === 'auto' ? '自动压缩中…' : '压缩中…'
  if (value.status === 'failed') return value.error || '压缩失败'
  return '压缩完成'
})

function onFieldSelect(form: FormInfo, fieldId: string, event: Event) {
  setFieldValue(form, fieldId, (event.target as HTMLSelectElement).value)
}

function onFieldInput(form: FormInfo, fieldId: string, event: Event) {
  setFieldValue(form, fieldId, (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="v2-chat">
    <header class="v2-head">
      <div class="v2-title">
        <strong>{{ runtime?.info?.title || '新会话' }}</strong>
        <span class="v2-status" :class="status">{{ status }}</span>
      </div>
      <div class="v2-head-actions">
        <button
          v-if="sessionId"
          class="v2-btn"
          :disabled="status === 'busy' || status === 'retry'"
          title="立即压缩上下文"
          @click="compact"
        >
          压缩
        </button>
        <button
          v-if="status === 'busy' || status === 'retry'"
          class="v2-btn danger"
          @click="stop"
        >
          停止
        </button>
      </div>
    </header>

    <p v-if="error || executionError" class="v2-error">{{ error || executionError }}</p>

    <div ref="scroller" class="v2-messages">
      <div v-if="!!sessionId && !messages.length && !runtime?.hydrated" class="v2-empty">载入会话…</div>
      <div v-else-if="!messages.length" class="v2-empty-state">
        <div class="empty-orb">
          <div class="empty-orb-core">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="12" rx="4"/><path d="M12 3v4M8 12h.01M16 12h.01M9 16h6"/></svg>
          </div>
          <div class="empty-orb-ring"></div>
        </div>
        <p class="empty-title">基于 RAG 的知识库问答与 Agent 系统</p>
        <p class="empty-hint">知识库检索、代码编写、联网搜索一站完成</p>
      </div>

      <div
        v-for="message in renderedMessages"
        :key="message.id"
        class="v2-message"
        :class="message.type"
      >
        <template v-if="message.type === 'user'">
          <div class="v2-avatar user">
            <img v-if="hasImageAvatar()" class="avatar-img" :src="auth.avatar" alt="" />
            <span v-else>{{ auth.avatar || userInitial }}</span>
          </div>
          <div class="v2-user-message">
            <div class="v2-bubble user">
              <audio
                v-if="message.voice"
                class="v2-voice"
                :src="message.voice.url"
                controls
              ></audio>
              <span>{{ message.text }}</span>
            </div>
            <div class="v2-user-actions">
              <button class="v2-copy" title="复制这条消息" @click="copyMessage(message)">
                {{ copiedId === message.id ? '已复制' : '复制' }}
              </button>
              <button class="v2-revert" title="撤销到此消息" @click="revertTo(message.id)">撤销到此</button>
            </div>
          </div>
        </template>

        <template v-else-if="message.type === 'assistant'">
          <div class="v2-avatar assistant">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="12" rx="4"/><path d="M12 3v4M8 12h.01M16 12h.01M9 16h6"/></svg>
          </div>
          <div class="v2-assistant">
            <template v-if="isActiveMessage(message)">
              <div class="v2-think" role="status" aria-label="思考中">
                <span class="v2-think-text">思考中</span>
              </div>
              <div v-if="hasExecInfo(message)" class="v2-exec">
                <div v-if="liveCompaction" class="v2-exec-row" :class="liveCompaction.status">
                  <span class="v2-exec-name">上下文压缩</span>
                  <span class="v2-exec-state">{{ compactionStateText }}</span>
                </div>
                <div v-if="liveCompaction?.text" class="v2-exec-note">{{ liveCompaction.text }}</div>
                <div
                  v-for="(part, index) in execReasoning(message)"
                  :key="`${message.id}-reason-${index}`"
                  class="v2-exec-reason"
                >
                  <span class="v2-exec-name">推理</span>
                  <p>{{ part.text }}</p>
                </div>
                <div
                  v-for="(part, index) in outputParts(message)"
                  :key="`${message.id}-output-${index}`"
                  class="v2-exec-output"
                >
                  <span class="v2-exec-name">输出</span>
                  <p>{{ part.text }}</p>
                </div>
              </div>
            </template>
            <template v-if="!isActiveMessage(message)">
              <div
                v-for="(part, index) in answerParts(message)"
                :key="`${message.id}-text-${index}`"
                class="v2-bubble assistant"
              >{{ part.text }}</div>
            </template>
            <div class="v2-message-actions">
              <button class="v2-copy" title="复制这条回复" @click="copyMessage(message)">
                {{ copiedId === message.id ? '已复制' : '复制' }}
              </button>
              <button class="v2-revert" title="查看本轮文件改动" @click="showDiff(message.id)">改动</button>
            </div>
            <div v-if="activeDiff?.messageId === message.id" class="v2-diff">
              <div class="v2-diff-actions"><button @click="restoreFiles(message.id)">还原文件改动</button></div>
              <div v-if="diffLoading" class="v2-diff-empty">生成 diff…</div>
              <div v-else-if="!activeDiff.diff.files.length" class="v2-diff-empty">
                本轮没有文件改动
              </div>
              <details v-for="file in activeDiff.diff.files" :key="file.file" open>
                <summary>{{ file.file }}</summary>
                <pre>{{ file.diff }}</pre>
              </details>
            </div>
          </div>
        </template>

        <template v-else-if="message.type === 'system'">
          <div class="v2-system">{{ message.text }}</div>
        </template>
      </div>

      <div v-if="executing && !activeAssistant" class="v2-message assistant v2-thinking-row">
        <div class="v2-avatar assistant">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="12" rx="4"/><path d="M12 3v4M8 12h.01M16 12h.01M9 16h6"/></svg>
        </div>
        <div class="v2-assistant">
          <div class="v2-think" role="status" aria-label="思考中">
            <span class="v2-think-text">思考中</span>
          </div>
          <div v-if="liveCompaction" class="v2-exec">
            <div class="v2-exec-row" :class="liveCompaction.status">
              <span class="v2-exec-name">上下文压缩</span>
              <span class="v2-exec-state">{{ compactionStateText }}</span>
            </div>
            <div v-if="liveCompaction.text" class="v2-exec-note">{{ liveCompaction.text }}</div>
          </div>
        </div>
      </div>

      <div v-if="inbox.length" class="v2-inbox">
        <div class="v2-inbox-title">待发送 {{ inbox.length }}</div>
        <div v-for="item in inbox" :key="item.id" class="v2-inbox-item">
          <span class="v2-inbox-text">
            {{ item.item.type === 'user' ? item.item.payload.text : item.item.type }}
          </span>
          <div class="v2-inbox-actions">
            <button @click="changeInbox(item, 'steer')">插队</button>
            <button @click="changeInbox(item, 'queue')">排队</button>
            <button @click="removeInbox(item)">取消</button>
          </div>
        </div>
      </div>

      <div v-if="permissions.length" class="v2-cards">
        <div v-for="request in permissions" :key="request.id" class="v2-permission">
          <div class="v2-card-title">需要权限 · {{ request.action }}</div>
          <p>{{ request.message || request.resources.join(', ') }}</p>
          <div class="v2-card-actions">
            <button @click="answerPermission(request, 'once')">允许一次</button>
            <button @click="answerPermission(request, 'always')">总是允许</button>
            <button class="danger" @click="answerPermission(request, 'reject')">拒绝</button>
          </div>
        </div>
      </div>

      <div v-if="forms.length" class="v2-cards">
        <div v-for="form in forms" :key="form.id" class="v2-form">
          <div class="v2-card-title">{{ form.title || '请选择' }}</div>
          <label v-for="field in form.fields" :key="field.id" class="v2-field">
            <span>{{ field.label || field.id }}</span>
            <select
              v-if="field.options.length"
              :value="fieldValue(form, field.id)"
              @change="onFieldSelect(form, field.id, $event)"
            >
              <option value="">请选择</option>
              <option v-for="option in field.options" :key="option" :value="option">{{ option }}</option>
            </select>
            <input
              v-else
              :value="fieldValue(form, field.id)"
              @input="onFieldInput(form, field.id, $event)"
            />
          </label>
          <div class="v2-card-actions">
            <button @click="submitForm(form)">提交</button>
          </div>
        </div>
      </div>
    </div>

    <footer class="chat-input">
      <div class="input-card">
        <div v-if="slashOpen" class="slash-mask" @click="closeSlash"></div>
        <div v-if="slashOpen" class="slash-panel">
          <div class="slash-head">
            <span class="slash-title">技能与命令</span>
            <button class="slash-close" type="button" title="关闭" @click="closeSlash">✕</button>
          </div>
          <input
            ref="slashSearchRef"
            v-model="slashSearch"
            class="slash-search"
            type="text"
            placeholder="搜索技能或命令…（↑↓ 选择，Enter 插入，Esc 关闭）"
            @keydown="onSlashSearchKeydown"
          />
          <div v-if="(skillStore.loading || commandsLoading) && !slashFiltered.length" class="slash-empty">加载中…</div>
          <div v-else-if="!slashFiltered.length" class="slash-empty">没有匹配的技能或命令</div>
          <div v-else ref="slashListRef" class="slash-list">
            <button
              v-for="(item, index) in slashFiltered"
              :key="`${item.kind}-${item.name}`"
              type="button"
              class="slash-item"
              :class="{ active: index === slashActive }"
              @mousemove="slashActive = index"
              @click="slashUse(item)"
            >
              <span class="slash-name">/{{ item.name }}</span>
              <span v-if="item.kind === 'command'" class="slash-kind">命令</span>
              <span class="slash-desc">{{ item.description || '暂无描述' }}</span>
            </button>
          </div>
        </div>
        <div v-if="pendingFiles.length || pendingVoice" class="pending-files">
          <span v-if="pendingVoice" class="msg-file-name">
            语音 {{ pendingVoice.duration.toFixed(1) }}s
            <button type="button" title="移除语音" @click="pendingVoice = null">×</button>
          </span>
          <span v-for="(file, index) in pendingFiles" :key="`${file.name}-${index}`" class="msg-file-name">
            {{ file.name }}
            <button type="button" title="移除附件" @click="removeFile(index)">×</button>
          </span>
        </div>
        <textarea
          ref="composerRef"
          v-model="draft"
          rows="2"
          placeholder="输入消息，Enter 发送，Shift+Enter 换行；/skills 选择技能"
          @keydown="onComposerKeydown"
        ></textarea>
        <div class="input-actions">
          <input ref="fileInput" type="file" multiple hidden @change="onFiles" />
          <button class="attach-btn" type="button" title="添加附件" @click="chooseFiles">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m21.4 11.6-8.9 8.9a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg>
          </button>
          <div class="ref-picker">
            <button class="attach-btn" type="button" title="插入引用" @click="referenceMenuOpen = !referenceMenuOpen">
              @
            </button>
            <div v-if="referenceMenuOpen" class="ref-menu">
              <button
                v-for="item in referenceItems"
                :key="item.name"
                type="button"
                class="ref-item"
                @click="useReference(item.name)"
              >
                <strong>{{ item.name }}</strong>
                <span>{{ item.kind }} · {{ item.status }}</span>
                <small>{{ item.description }}</small>
              </button>
              <div v-if="!referenceItems.length" class="ref-empty">未配置引用</div>
            </div>
          </div>
          <button
            class="mic-btn"
            :class="{ recording }"
            type="button"
            :disabled="voiceBusy"
            :title="recording ? '停止录音' : '语音输入'"
            @click="toggleVoice"
          >
            <svg v-if="!recording" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 10.5a6.5 6.5 0 0 0 13 0M12 17v4.5M8.5 21.5h7"/></svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
          </button>
          <span v-if="recording" class="record-hint">{{ recordSeconds.toFixed(0) }}s</span>
          <div
            class="model-wrap"
            @mouseenter="agentTipVisible = true"
            @mouseleave="agentTipVisible = false"
          >
            <button
              class="model-pill"
              type="button"
              :title="currentAgentDesc"
              @click.stop="agentMenuOpen = !agentMenuOpen"
            >
              <span class="model-current">{{ currentAgentLabel }}</span>
              <svg class="model-chevron" :class="{ open: agentMenuOpen }" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m4 6.5 4 4 4-4"/></svg>
            </button>
            <div v-if="agentMenuOpen" class="model-menu-mask" @click="agentMenuOpen = false"></div>
            <div v-if="agentMenuOpen" class="model-menu">
              <div class="model-menu-head">
                <span>Agent</span>
                <b>{{ currentAgentLabel }}</b>
              </div>
              <div class="model-menu-section">选择 Agent</div>
              <button
                v-for="agent in agentOptions"
                :key="agent.name"
                class="model-menu-item"
                :class="{ active: agent.name === currentAgentName }"
                type="button"
                @click="pickAgent(agent.name)"
              >
                <span class="model-menu-text">
                  <span class="model-menu-name">{{ agent.name }}</span>
                  <span class="model-menu-desc">{{ agent.description || agent.mode }}</span>
                </span>
                <svg v-if="agent.name === currentAgentName" class="check" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m3.5 8.5 3 3 6-7"/></svg>
              </button>
              <div v-if="!agentOptions.length" class="model-menu-section">没有可用 Agent</div>
            </div>
            <div v-if="agentTipVisible && !agentMenuOpen && currentAgentDesc" class="model-tip">
              <div class="model-tip-name">{{ currentAgentLabel }}</div>
              <span class="model-tip-desc">{{ currentAgentDesc }}</span>
            </div>
          </div>
          <div
            class="model-wrap"
            @mouseenter="modelTipVisible = true"
            @mouseleave="modelTipVisible = false"
          >
            <button
              class="model-pill"
              type="button"
              :disabled="!models.modelOptions.length"
              :title="currentModelDesc"
              @click.stop="modelMenuOpen = !modelMenuOpen"
            >
              <span class="model-current">{{ currentModelLabel }}</span>
              <svg class="model-chevron" :class="{ open: modelMenuOpen }" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m4 6.5 4 4 4-4"/></svg>
            </button>
            <div v-if="modelMenuOpen" class="model-menu-mask" @click="modelMenuOpen = false"></div>
            <div v-if="modelMenuOpen" class="model-menu">
              <div class="model-menu-head">
                <span>模型</span>
                <b>{{ currentModelLabel }}</b>
              </div>
              <div class="model-menu-section">选择模型</div>
              <button
                v-for="option in models.modelOptions"
                :key="option.value"
                class="model-menu-item"
                :class="{ active: option.value === effectiveModelId }"
                type="button"
                @click="pickModel(option.value)"
              >
                <span class="model-menu-text">
                  <span class="model-menu-name">{{ option.label }}</span>
                  <span class="model-menu-desc">{{ option.desc }}</span>
                </span>
                <svg v-if="option.value === effectiveModelId" class="check" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m3.5 8.5 3 3 6-7"/></svg>
              </button>
            </div>
            <div v-if="modelTipVisible && !modelMenuOpen && currentModelDesc" class="model-tip">
              <div class="model-tip-name">{{ currentModelLabel }}</div>
              <span class="model-tip-desc">{{ currentModelDesc }}</span>
            </div>
          </div>
          <span class="actions-spacer"></span>
          <button class="send-btn" :disabled="!canSend" :title="sending ? '发送中' : '发送'" @click="send">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/></svg>
          </button>
        </div>
      </div>
    </footer>
  </div>
</template>

<style scoped src="../styles/v2Chat.css"></style>
