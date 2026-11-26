<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  cancelForm,
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
import { ApiError } from '../api/errors'
import FormCard from '../v2/FormCard.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import type {
  AssistantMessage,
  FileAttachment,
  FormInfo,
  InboxInfo,
  MessagePart,
  MessageV2,
  PermissionRequest,
  PlanStep,
  ReasoningPart,
  TextPart,
  VoiceAttachment,
} from '../contracts/v2'
import { uploadVoiceMessage, synthesize, speakNative, stopNative } from '../api/voice'
import { PressToTalkRecorder } from '../voice/recorder'
import { useModelManagerStore } from '../stores/modelManager'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useAuthStore } from '../stores/auth'
import { useSkillStore } from '../stores/skills'
import { useChatSettingsStore } from '../stores/chatSettings'
import { useV2SessionStore } from '../v2/sessionStore'
import { isPlanVisible } from '../v2/runtime'
import { useStickToBottom } from '../v2/useStickToBottom'


const route = useRoute()
const router = useRouter()
const v2 = useV2SessionStore()
const models = useModelManagerStore()
const legacy = useMultiAgentStore()
const auth = useAuthStore()
const skillStore = useSkillStore()
const chatSettings = useChatSettingsStore()

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
const activeDiff = ref<{ messageId: string; diff: TurnDiff } | null>(null)
const diffLoading = ref(false)
const copiedId = ref('')
const inboxBusy = ref<Record<string, string>>({})
const inboxDone = ref<Record<string, string>>({})
const speakingId = ref('')
const speakingPaused = ref(false)
const speakingMode = ref<'audio' | 'native' | ''>('')
const speakingTime = ref(0)
const speakingDuration = ref(0)
interface SpeakSegment {
  part: number
  start: number
  end: number
  globalStart: number
  globalEnd: number
}
const speakingSegments = ref<SpeakSegment[]>([])
const speakingLead = ref(0)
const speakingTrimmedLength = ref(0)
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
const forms = computed(() => runtime.value?.forms.filter((form) => form.state === 'pending') ?? [])
const status = computed(() => runtime.value?.status?.type ?? 'idle')
const executionError = computed(() => runtime.value?.error?.message || '')
const localErrorTimer = ref<ReturnType<typeof setTimeout> | null>(null)
watch(error, (value) => {
  if (localErrorTimer.value) {
    clearTimeout(localErrorTimer.value)
    localErrorTimer.value = null
  }
  if (!value) return
  localErrorTimer.value = setTimeout(() => {
    error.value = ''
    localErrorTimer.value = null
  }, 15_000)
})
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
  if (!agentOptions.value.some(agent => agent.name === 'rag')) {
    agentOptions.value.push({
      name: 'rag',
      description: '知识库问答：检索向量库并基于证据回答。',
      mode: 'primary',
      tier: 'medium',
      model: '',
      tools: [],
      permission: {},
      request: {},
      hidden: false,
      disabled: false,
      color: '',
      steps: 0,
      source: 'builtin',
    })
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
const userInitial = computed(() => (auth.displayName || 'U').slice(0, 1).toUpperCase())

function hasImageAvatar(): boolean {
  return !!auth.avatar && (auth.avatar.startsWith('data:') || auth.avatar.startsWith('http'))
}
const canSend = computed(
  () =>
    (draft.value.trim().length > 0 || pendingFiles.value.length > 0 || pendingVoice.value !== null) &&
    !sending.value,
)

const scrollSignal = computed(() =>
  messages.value
    .map((message) => {
      const parts = message.type === 'assistant' ? message.content : []
      const signature = parts
        .map((part) => (part.type === 'tool' ? `${part.id}:${part.state.status}` : `${part.type}:${part.text.length}`))
        .join(',')
      return `${message.id}:${signature}`
    })
    .join('|'),
)
const { isNearTop, isNearBottom, scrollDirection, onScroll, scrollToBottom, scrollToTop } = useStickToBottom(
  scroller,
  () => `${scrollSignal.value}|${status.value}`,
)
const showTopBtn = computed(() => scrollDirection.value === 'up' && !isNearTop.value)
const showBottomBtn = computed(() => scrollDirection.value === 'down' && !isNearBottom.value)

// ── `/skills` 技能/命令与 `@` Agent 选择（参考 /skills 交互）────────────
// 输入 `/skills` 弹出技能/命令；输入 `@` 弹出可用 Agent（如 general/explore）并插入 `@name`。
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
const atToken = computed(() => {
  const match = /(^|\s)(@\S*)$/.exec(draft.value)
  if (!match) return null
  return { start: draft.value.length - match[2].length, token: match[2] }
})
const agentTrigger = computed(() => (atToken.value ? atToken.value.start : -1))
const agentRest = computed(() => (atToken.value ? atToken.value.token.slice(1) : ''))
const pickerMode = computed<'slash' | 'agent' | null>(() => {
  if (slashTrigger.value >= 0) return 'slash'
  if (agentTrigger.value >= 0) return 'agent'
  return null
})
interface SlashItem {
  name: string
  description: string
  kind: 'skill' | 'command' | 'agent'
}
const commandItems = ref<SlashCommandInfo[]>([])
const commandsLoading = ref(false)
const agentsLoading = ref(false)
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
const AGENT_MENTION_ALLOW = ['general', 'explore'] as const
const AGENT_MENTION_DEFAULT_DESC: Record<string, string> = {
  general: '通用任务处理',
  explore: '代码库探索与搜索',
}
const agentPickItems = computed<SlashItem[]>(() => {
  const byName = new Map(agentOptions.value.map((agent) => [agent.name, agent]))
  return AGENT_MENTION_ALLOW.map((name) => {
    const agent = byName.get(name)
    return {
      name,
      description: agent?.description || AGENT_MENTION_DEFAULT_DESC[name] || agent?.mode || '',
      kind: 'agent' as const,
    }
  })
})
const pickerSource = computed(() => (pickerMode.value === 'agent' ? agentPickItems.value : slashAll.value))
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
const slashOpen = computed(() => !slashDismissed.value && pickerMode.value !== null)
const slashQuery = computed(() => {
  const typed = slashSearch.value.trim()
  if (typed) return typed.toLowerCase()
  const rest = pickerMode.value === 'agent' ? agentRest.value : slashRest.value
  return rest.trim().toLowerCase()
})
const slashFiltered = computed(() => {
  const query = slashQuery.value
  const available = pickerSource.value
  if (!query) return available
  return available.filter(
    (item) =>
      item.name.toLowerCase().includes(query)
      || item.description.toLowerCase().includes(query),
  )
})

watch([slashOpen, pickerMode], ([open, mode]) => {
  if (!open) return
  slashActive.value = 0
  slashSearch.value = ''
  if (mode === 'slash') {
    if (!skillStore.initialized) void skillStore.fetchAll()
    void ensureCommands()
  } else if (mode === 'agent') {
    agentsLoading.value = true
    void ensureAgents().finally(() => { agentsLoading.value = false })
  }
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
  const mode = pickerMode.value
  const i = mode === 'agent' ? agentTrigger.value : slashTrigger.value
  const prefix = i >= 0 ? draft.value.slice(0, i) : ''
  const token = mode === 'agent' ? `@${item.name}` : `/${item.name}`
  draft.value = `${prefix}${token} `
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

async function submitForm(form: FormInfo, response: Record<string, unknown>) {
  const id = sessionId.value
  if (!id) return
  try {
    await replyForm(id, form.id, response, form.revision)
    v2.removeForm(id, form.id, form.revision + 1)
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      v2.removeForm(id, form.id, form.revision)
      return
    }
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function cancelFormRequest(form: FormInfo) {
  const id = sessionId.value
  if (!id) return
  try {
    await cancelForm(id, form.id, form.revision)
    v2.removeForm(id, form.id, form.revision + 1)
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      v2.removeForm(id, form.id, form.revision)
      return
    }
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function setInboxFlag(kind: 'busy' | 'done', id: string, value: string): void {
  const target = kind === 'busy' ? inboxBusy : inboxDone
  target.value = { ...target.value, [id]: value }
}

function clearInboxFlag(kind: 'busy' | 'done', id: string, value: string): void {
  const target = kind === 'busy' ? inboxBusy : inboxDone
  if (target.value[id] !== value) return
  const next = { ...target.value }
  delete next[id]
  target.value = next
}

function flashInboxDone(id: string, value: string): void {
  setInboxFlag('done', id, value)
  window.setTimeout(() => clearInboxFlag('done', id, value), 1500)
}

async function changeInbox(item: InboxInfo, delivery: 'steer' | 'queue') {
  const id = sessionId.value
  if (!id || inboxBusy.value[item.id]) return
  setInboxFlag('busy', item.id, delivery)
  try {
    const updated = await updateInboxDelivery(id, item.id, delivery)
    v2.setInbox(id, updated)
    flashInboxDone(item.id, delivery)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    flashInboxDone(item.id, 'failed')
  } finally {
    clearInboxFlag('busy', item.id, delivery)
  }
}

async function removeInbox(item: InboxInfo) {
  const id = sessionId.value
  if (!id || inboxBusy.value[item.id]) return
  setInboxFlag('busy', item.id, 'cancel')
  try {
    await cancelInbox(id, item.id)
    v2.removeInbox(id, item.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    flashInboxDone(item.id, 'failed')
  } finally {
    clearInboxFlag('busy', item.id, 'cancel')
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
const livePlan = computed(() => {
  if (!executing.value) return null
  const plan = runtime.value?.plan ?? null
  return isPlanVisible(plan) ? plan : null
})
const planProgress = computed(() => {
  const steps = livePlan.value?.steps ?? []
  return {
    done: steps.filter(step => step.status === 'completed').length,
    total: steps.length,
  }
})

function planStatusText(status: PlanStep['status']): string {
  if (status === 'completed') return '完成'
  if (status === 'in_progress') return '进行中'
  if (status === 'cancelled') return '取消'
  return '待执行'
}

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

const prevExecuting = ref(false)
const spokenKeys = new Set<string>()
let currentTtsAudio: HTMLAudioElement | null = null

function resetSpeakingState() {
  speakingId.value = ''
  speakingPaused.value = false
  speakingMode.value = ''
  speakingTime.value = 0
  speakingDuration.value = 0
  speakingSegments.value = []
  speakingLead.value = 0
  speakingTrimmedLength.value = 0
}

function stopAutoRead() {
  stopNative()
  if (currentTtsAudio) {
    try { currentTtsAudio.pause() } catch { /* noop */ }
    const src = currentTtsAudio.src
    currentTtsAudio = null
    if (src && src.startsWith('blob:')) {
      try { URL.revokeObjectURL(src) } catch { /* noop */ }
    }
  }
  resetSpeakingState()
}

function fmtSpeakTime(sec: number) {
  if (!Number.isFinite(sec) || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function speakingPercent(messageId: string) {
  if (speakingId.value !== messageId) return 0
  const d = speakingDuration.value
  if (!d || !Number.isFinite(d)) return 0
  return Math.max(0, Math.min(100, (speakingTime.value / d) * 100))
}

/** Split text into sentence-sized ranges for spoken-text highlighting. */
function sentenceRanges(text: string): { start: number; end: number }[] {
  const ranges: { start: number; end: number }[] = []
  const push = (start: number, end: number) => {
    if (text.slice(start, end).trim()) ranges.push({ start, end })
  }
  let start = 0
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    const cjkEnd = ch === '。' || ch === '！' || ch === '？' || ch === '；' || ch === '\n'
    const asciiEnd = (ch === '.' || ch === '!' || ch === '?' || ch === ';')
      && (i + 1 >= text.length || /\s/.test(text[i + 1]))
    if (cjkEnd || asciiEnd) {
      push(start, i + 1)
      start = i + 1
    }
  }
  if (start < text.length) push(start, text.length)
  return ranges.length ? ranges : (text.trim() ? [{ start: 0, end: text.length }] : [])
}

/**
 * Build the spoken-text segment map for a message. Audio TTS has no timestamps,
 * so the active sentence is estimated from playback progress by character
 * count; native speech uses `onboundary` char indices.
 */
function prepareSpeaking(message: MessageV2 | null, spoken: string) {
  const parts = message ? answerParts(message) : []
  const full = parts.length ? parts.map((part) => part.text).join('\n\n') : spoken
  speakingLead.value = full.length - full.trimStart().length
  speakingTrimmedLength.value = full.trim().length || spoken.length
  const segments: SpeakSegment[] = []
  if (parts.length) {
    let base = 0
    parts.forEach((part, partIndex) => {
      for (const range of sentenceRanges(part.text)) {
        segments.push({
          part: partIndex,
          start: range.start,
          end: range.end,
          globalStart: base + range.start,
          globalEnd: base + range.end,
        })
      }
      base += part.text.length + 2
    })
  } else {
    for (const range of sentenceRanges(spoken)) {
      segments.push({
        part: 0,
        start: range.start,
        end: range.end,
        globalStart: range.start,
        globalEnd: range.end,
      })
    }
  }
  speakingSegments.value = segments
}

const speakingActiveSegment = computed(() => {
  const segments = speakingSegments.value
  if (!segments.length) return -1
  let offset: number
  if (speakingMode.value === 'audio') {
    const duration = speakingDuration.value
    if (!duration || !Number.isFinite(duration)) return 0
    offset = speakingLead.value + Math.round((speakingTime.value / duration) * speakingTrimmedLength.value)
  } else if (speakingMode.value === 'native') {
    offset = speakingLead.value + Math.round(speakingTime.value)
  } else {
    return -1
  }
  for (let i = 0; i < segments.length; i += 1) {
    if (offset >= segments[i].globalStart && offset < segments[i].globalEnd) return i
  }
  return offset >= segments[segments.length - 1].globalEnd ? segments.length - 1 : 0
})

function speakSpans(message: MessageV2, partIndex: number, partText: string): { text: string; active: boolean }[] {
  const plain = [{ text: partText, active: false }]
  if (speakingId.value !== message.id) return plain
  const active = speakingActiveSegment.value
  const segments = speakingSegments.value
  if (active < 0 || active >= segments.length) return plain
  const segment = segments[active]
  if (segment.part !== partIndex) return plain
  const spans: { text: string; active: boolean }[] = []
  if (segment.start > 0) spans.push({ text: partText.slice(0, segment.start), active: false })
  spans.push({ text: partText.slice(segment.start, segment.end), active: true })
  if (segment.end < partText.length) spans.push({ text: partText.slice(segment.end), active: false })
  return spans
}

function bindAudioProgress(audio: HTMLAudioElement, id: string, url: string) {
  speakingMode.value = 'audio'
  speakingPaused.value = false
  speakingTime.value = 0
  speakingDuration.value = Number.isFinite(audio.duration) ? audio.duration : 0
  audio.onloadedmetadata = () => {
    if (speakingId.value === id) speakingDuration.value = Number.isFinite(audio.duration) ? audio.duration : 0
  }
  audio.ontimeupdate = () => {
    if (speakingId.value === id) speakingTime.value = audio.currentTime || 0
  }
  audio.onplay = () => { if (speakingId.value === id) speakingPaused.value = false }
  audio.onpause = () => {
    if (speakingId.value === id && currentTtsAudio === audio && !audio.ended) speakingPaused.value = true
  }
  audio.onended = () => {
    if (currentTtsAudio === audio) currentTtsAudio = null
    if (speakingId.value === id) resetSpeakingState()
    if (url.startsWith('blob:')) {
      try { URL.revokeObjectURL(url) } catch { /* noop */ }
    }
  }
}

function startNativeSpeak(text: string, id: string) {
  speakingMode.value = 'native'
  speakingPaused.value = false
  speakingTime.value = 0
  speakingDuration.value = text.length
  try {
    const synth = window.speechSynthesis
    if (!synth) { resetSpeakingState(); return }
    synth.cancel()
    const u = new SpeechSynthesisUtterance(text)
    const lang = ({ Auto: 'zh-CN', Chinese: 'zh-CN', English: 'en-US', Japanese: 'ja-JP', Korean: 'ko-KR', French: 'fr-FR', German: 'de-DE', Spanish: 'es-ES', Portuguese: 'pt-PT', Russian: 'ru-RU', Italian: 'it-IT' } as Record<string,string>)[chatSettings.ttsLang || 'Auto'] || 'zh-CN'
    u.lang = lang
    try {
      const primary = lang.split('-')[0].toLowerCase()
      const pick = synth.getVoices().find(v => (v.lang || '').toLowerCase().startsWith(primary))
      if (pick) u.voice = pick
    } catch { /* noop */ }
    u.onboundary = (e) => {
      if (speakingId.value !== id) return
      const idx = (e as any).charIndex ?? 0
      speakingTime.value = Math.max(speakingTime.value, idx)
    }
    u.onend = () => { if (speakingId.value === id) resetSpeakingState() }
    u.onerror = () => { if (speakingId.value === id) resetSpeakingState() }
    synth.speak(u)
  } catch {
    if (speakingId.value === id) resetSpeakingState()
  }
}

async function autoReadText(text: string, id = '') {
  const content = (text || '').trim()
  if (!content) return
  stopAutoRead()
  speakingId.value = id
  const message = id ? messages.value.find((item) => item.id === id) ?? null : null
  prepareSpeaking(message, content)
  try {
    const url = await synthesize(content, undefined, chatSettings.ttsLang || 'Auto')
    const audio = new Audio(url)
    currentTtsAudio = audio
    bindAudioProgress(audio, id, url)
    await audio.play()
  } catch {
    if (!chatSettings.autoRead && !id) {
      resetSpeakingState()
      return
    }
    startNativeSpeak(content, id)
  }
}

async function speakMessage(message: MessageV2) {
  const text = messageText(message).trim()
  if (!text) return
  if (speakingId.value === message.id) {
    if (speakingMode.value === 'audio' && currentTtsAudio) {
      if (currentTtsAudio.paused) { try { await currentTtsAudio.play() } catch { /* noop */ } }
      else { try { currentTtsAudio.pause() } catch { /* noop */ } }
      return
    }
    if (speakingMode.value === 'native') {
      try {
        const synth = window.speechSynthesis
        if (synth?.speaking && !synth.paused) { synth.pause(); speakingPaused.value = true }
        else if (synth?.paused) { synth.resume(); speakingPaused.value = false }
      } catch { /* noop */ }
      return
    }
    stopAutoRead()
    return
  }
  stopAutoRead()
  speakingId.value = message.id
  prepareSpeaking(message, text)
  try {
    const url = await synthesize(text, undefined, chatSettings.ttsLang || 'Auto')
    const audio = new Audio(url)
    currentTtsAudio = audio
    bindAudioProgress(audio, message.id, url)
    await audio.play()
  } catch {
    startNativeSpeak(text, message.id)
  }
}

watch(sessionId, () => {
  prevExecuting.value = false
  stopAutoRead()
})

watch(
  [executing, () => status.value, () => runtime.value?.info?.outcome, () => messages.value.length],
  async () => {
    const was = prevExecuting.value
    prevExecuting.value = executing.value
    if (!sessionId.value || !chatSettings.autoRead) return
    if (!was || executing.value) return
    if (status.value !== 'idle') return
    if (runtime.value?.info?.outcome && runtime.value.info.outcome !== 'succeeded') return

    const last = [...renderedMessages.value].reverse().find(
      (m) => m.type === 'assistant' && !isActiveMessage(m),
    )
    if (!last) return
    const key = `${sessionId.value}:${last.id}`
    if (spokenKeys.has(key)) return
    const text = messageText(last).trim()
    if (!text) return
    spokenKeys.add(key)
    await autoReadText(text, last.id)
  },
)

onBeforeUnmount(() => {
  if (localErrorTimer.value) clearTimeout(localErrorTimer.value)
  stopAutoRead()
})

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

    <div class="v2-timeline">
    <div ref="scroller" class="v2-messages" @scroll="onScroll">
      <div v-if="!messages.length" class="v2-empty-state">
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
              <div v-if="livePlan" class="v2-plan-card">
                <details open>
                  <summary>
                    <span class="v2-plan-title">执行计划</span>
                    <span class="v2-plan-progress">{{ planProgress.done }}/{{ planProgress.total }}</span>
                  </summary>
                  <ol class="v2-plan-steps">
                    <li v-for="step in livePlan.steps" :key="step.id" :class="step.status">
                      <span class="v2-plan-status" :class="step.status">{{ planStatusText(step.status) }}</span>
                      <span class="v2-plan-text">{{ step.title }}</span>
                      <span v-if="step.agent" class="v2-plan-agent">{{ step.agent }}</span>
                    </li>
                  </ol>
                </details>
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
              >
                <MarkdownContent v-if="speakingId !== message.id" :text="part.text" />
                <template v-else>
                  <span
                    v-for="(span, spanIndex) in speakSpans(message, index, part.text)"
                    :key="spanIndex"
                    :class="{ 'v2-tts-active': span.active }"
                  >{{ span.text }}</span>
                </template>
              </div>
            </template>
            <div class="v2-message-actions">
              <button class="v2-copy" title="复制完整回复" @click="copyMessage(message)">
                {{ copiedId === message.id ? '已复制' : '复制' }}
              </button>
              <button
                v-if="answerParts(message).some(p => p.text.trim())"
                class="v2-copy"
                :title="speakingId === message.id ? (speakingPaused ? '继续朗读' : '暂停朗读') : '朗读回复'"
                @click="speakMessage(message)"
              >
                {{ speakingId === message.id ? (speakingPaused ? '继续' : '暂停') : '朗读' }}
              </button>
              <button
                v-if="speakingId === message.id"
                class="v2-copy"
                title="停止朗读"
                @click="stopAutoRead()"
              >停止</button>
              <button class="v2-revert" title="查看本轮文件改动" @click="showDiff(message.id)">改动</button>
            </div>
            <div v-if="speakingId === message.id" class="v2-tts-progress" aria-label="朗读进度">
              <div class="v2-tts-bar"><div class="v2-tts-bar-fill" :style="{ width: speakingPercent(message.id) + '%' }"></div></div>
              <span class="v2-tts-time">
                {{ speakingMode === 'audio' ? fmtSpeakTime(speakingTime) : Math.round(speakingPercent(message.id)) + '%' }}
                <template v-if="speakingMode === 'audio' && speakingDuration"> / {{ fmtSpeakTime(speakingDuration) }}</template>
                <template v-if="speakingMode === 'native'"> · 浏览器朗读</template>
              </span>
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

        <template v-else-if="message.type === 'synthetic'">
          <!-- <div class="v2-warning-card" role="status">
            <div class="v2-warning-title">
              {{ message.description === 'action_not_executed' ? '操作未执行' : '系统提示' }}
            </div>
            <div class="v2-warning-text">{{ message.text }}</div>
          </div> -->
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
          <div v-if="livePlan" class="v2-plan-card">
            <details open>
              <summary>
                <span class="v2-plan-title">执行计划</span>
                <span class="v2-plan-progress">{{ planProgress.done }}/{{ planProgress.total }}</span>
              </summary>
              <ol class="v2-plan-steps">
                <li v-for="step in livePlan.steps" :key="step.id" :class="step.status">
                  <span class="v2-plan-status" :class="step.status">{{ planStatusText(step.status) }}</span>
                  <span class="v2-plan-text">{{ step.title }}</span>
                  <span v-if="step.agent" class="v2-plan-agent">{{ step.agent }}</span>
                </li>
              </ol>
            </details>
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
            <button
              :class="{ active: item.item.delivery === 'steer', done: inboxDone[item.id] === 'steer' }"
              :disabled="!!inboxBusy[item.id] || item.item.delivery === 'steer'"
              @click="changeInbox(item, 'steer')"
            >
              {{
                inboxDone[item.id] === 'failed' ? '失败'
                  : inboxBusy[item.id] === 'steer' ? '处理中…'
                    : item.item.delivery === 'steer' ? '已插队' : '插队'
              }}
            </button>
            <button
              :class="{ active: item.item.delivery === 'queue', done: inboxDone[item.id] === 'queue' }"
              :disabled="!!inboxBusy[item.id] || item.item.delivery === 'queue'"
              @click="changeInbox(item, 'queue')"
            >
              {{
                inboxDone[item.id] === 'failed' ? '失败'
                  : inboxBusy[item.id] === 'queue' ? '处理中…'
                    : item.item.delivery === 'queue' ? '已排队' : '排队'
              }}
            </button>
            <button
              :disabled="!!inboxBusy[item.id]"
              @click="removeInbox(item)"
            >
              {{ inboxBusy[item.id] === 'cancel' ? '处理中…' : '取消' }}
            </button>
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
        <FormCard
          v-for="form in forms"
          :key="form.id"
          :form="form"
          @submit="response => submitForm(form, response)"
          @cancel="cancelFormRequest(form)"
        />
      </div>
    </div>
    <div class="scroll-controls">
      <transition name="scroll-fade">
        <button
          v-if="showTopBtn"
          class="scroll-control-btn scroll-to-top-btn"
          type="button"
          title="回到顶部"
          aria-label="回到顶部"
          @click="scrollToTop('auto')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M5.5 11.5 12 5l6.5 6.5"/></svg>
        </button>
      </transition>
      <transition name="scroll-fade">
        <button
          v-if="showBottomBtn"
          class="scroll-control-btn scroll-to-bottom-btn"
          type="button"
          title="回到底部"
          aria-label="回到底部"
          @click="scrollToBottom('auto')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M19 12l-7 7-7-7"/></svg>
        </button>
      </transition>
    </div>
    </div>

    <footer class="chat-input">
      <div class="input-card">
        <div v-if="slashOpen" class="slash-mask" @click="closeSlash"></div>
        <div v-if="slashOpen" class="slash-panel">
          <div class="slash-head">
            <span class="slash-title">{{ pickerMode === 'agent' ? 'Agent' : '技能与命令' }}</span>
            <button class="slash-close" type="button" title="关闭" @click="closeSlash">✕</button>
          </div>
          <input
            ref="slashSearchRef"
            v-model="slashSearch"
            class="slash-search"
            type="text"
            :placeholder="pickerMode === 'agent' ? '搜索 Agent…（↑↓ 选择，Enter 插入，Esc 关闭）' : '搜索技能或命令…（↑↓ 选择，Enter 插入，Esc 关闭）'"
            @keydown="onSlashSearchKeydown"
          />
          <div v-if="(pickerMode === 'agent' ? agentsLoading : (skillStore.loading || commandsLoading)) && !slashFiltered.length" class="slash-empty">加载中…</div>
          <div v-else-if="!slashFiltered.length" class="slash-empty">{{ pickerMode === 'agent' ? '没有可用 Agent' : '没有匹配的技能或命令' }}</div>
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
              <span class="slash-name">{{ pickerMode === 'agent' ? `@${item.name}` : `/${item.name}` }}</span>
              <span v-if="item.kind === 'command'" class="slash-kind">命令</span>
              <span v-else-if="item.kind === 'agent'" class="slash-kind">Agent</span>
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
          placeholder="输入消息，Enter 发送，Shift+Enter 换行；/skills 选技能，@ 选 Agent"
          @keydown="onComposerKeydown"
        ></textarea>
        <div class="input-actions">
          <input ref="fileInput" type="file" multiple hidden @change="onFiles" />
          <button class="attach-btn" type="button" title="添加附件" @click="chooseFiles">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m21.4 11.6-8.9 8.9a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg>
          </button>

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
