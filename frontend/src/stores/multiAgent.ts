import { defineStore } from 'pinia'
import { ref, computed, reactive, watch, onScopeDispose } from 'vue'
import type { MultiAgentMessage, AgentStreamData, MultiAgentSSEEvent, ChatError, FileContent, VoiceMessageData, QueuedPrompt } from '../types'
import {
  sendMultiAgentStream,
  restoreSnapshot as apiRestoreSnapshot,
  fetchTurnDiff as apiFetchTurnDiff,
} from '../api/multiAgent'
import {
  listConversations,
  getConversation,
  renameConversation as apiRenameConversation,
  deleteConversation as apiDeleteConversation,
  enqueueSessionPrompt,
  removeSessionPrompt,
  type ConversationMeta,
} from '../api/sessions'
import { SUPPORTED_MODELS } from '../config/models'
import { fetchModels, modelsToOptions, type ModelInfo, type ModelOption } from '../api/models'
import {
  saveSessionToCache,
  loadSessionFromCache,
  deleteSessionFromCache,
} from '../api/session-cache'
import {
  interruptSession, revertSession, deleteSessionMessage, updateSession as apiUpdateSession,
  forkSession as apiForkSession, compactSession as apiCompactSession, getSessionStatus as apiGetSessionStatus,
  type SessionStatus,
} from '../api/sessions'
import { loadModelCache, saveModelCache } from '../api/model-cache'
import { classifyNetworkError } from '../api/errors'
import { useHarnessTurn } from '../harness/useHarnessTurn'
import { usePermissionStore } from './permission'

function genId(): string {
  try { return crypto.randomUUID() }
  catch { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16) }) }
}

// ——— Agent parts 累积（对齐 opencode Part 排序）———
// 子 Agent 的输出按发送顺序记入 parts 数组：正文增量归并进相邻 text part，
// 工具调用追加/原位更新 tool part；seq 单调递增保证顺序稳定，组件按序渲染。

/** 下一个文本增量均并入最后一个 text part；否则新建一个 text part 追加到流尾。 */
interface SessionState {
  messages: MultiAgentMessage[]
  conversationId: string | undefined
  conversationTitle: string
  loading: boolean
  abortController: AbortController | null
  streamPhase: 'idle' | 'queued' | 'running'
  queuePosition: number | null
  /** [队列] 本会话「执行中追加」的待处理任务（后端 session_inputs；新→旧执行） */
  pendingPrompts: QueuedPrompt[]
}

export const useMultiAgentStore = defineStore('multiAgent', () => {
  const sessions = ref<Record<string, SessionState>>({})
  const activeSessionId = ref<string | undefined>(undefined)
  const conversations = ref<ConversationMeta[]>([])
  const routingStatus = ref<string>('')
  const selectedModel = ref<string>(SUPPORTED_MODELS[0].value)
  // [模型目录] 选择器由后端 GET /api/models 动态驱动；SUPPORTED_MODELS 仅为离线兜底。
  const models = ref<ModelInfo[]>([])
  const defaultModelId = ref<string>('')
  // [模型管理] 轻量模型（内部轻任务：子任务分类/会话标题/计划摘要）
  const smallModelId = ref<string>('')
  const smallModelInfo = computed<ModelInfo | null>(() =>
    models.value.find(m => m.id === smallModelId.value) ?? null,
  )
  // [模型管理] 图片解析模型 / 语音模型规格（模型管理页可配，含全部 Provider）
  const imageCaptionModelId = ref<string>('')
  const voiceModelSize = ref<string>('')
  const imageCaptionModelInfo = computed<ModelInfo | null>(() =>
    models.value.find(m => m.id === imageCaptionModelId.value) ?? null,
  )
  const modelOptions = computed<ModelOption[]>(() =>
    models.value.length
      ? modelsToOptions(models.value)
      : SUPPORTED_MODELS.map(m => ({ value: m.value, label: m.label, desc: m.desc })),
  )
  let modelsLoaded = false
  async function loadModels(force = false) {
    if (modelsLoaded && !force) return
    modelsLoaded = true
    // [perf] stale-while-revalidate：先用上次成功拉取的本地快照**同步**渲染模型下拉框，
    // 再后台等网络刷新。此前必须 await fetchModels() 才填充 models，而后端首次探测
    // provider（外网，各带超时）可达数秒 → 模型选择器整块空白等待。快照是纯本地读取，零延迟。
    const cache = loadModelCache()
    if (cache && cache.models.length) {
      models.value = cache.models
      defaultModelId.value = cache.default_model || ''
      smallModelId.value = cache.small_model || ''
      imageCaptionModelId.value = cache.image_caption_model || ''
      voiceModelSize.value = cache.voice_model_size || ''
      if (defaultModelId.value && !models.value.some(m => m.id === selectedModel.value)) {
        selectedModel.value = defaultModelId.value
      }
    }
    try {
      const res = await fetchModels()
      models.value = res.models || []
      defaultModelId.value = res.default_model || ''
      smallModelId.value = res.small_model || ''
      imageCaptionModelId.value = res.image_caption_model || ''
      voiceModelSize.value = res.voice_model_size || ''
      // 后端成功 → 保存到前端缓存，作为下次启动/离线兜底
      saveModelCache({
        savedAt: Date.now(),
        models: models.value,
        default_model: defaultModelId.value,
        small_model: smallModelId.value,
        image_caption_model: imageCaptionModelId.value,
        voice_model_size: voiceModelSize.value,
      })
      if (defaultModelId.value && models.value.length && !models.value.some(m => m.id === selectedModel.value)) {
        // 选中模型已不在目录（被删除）或首次选中 → 回退默认，避免无效模型继续发送
        selectedModel.value = defaultModelId.value
      }
    } catch (e) {
      console.error('Failed to load model catalog:', e)
      // 后端不可达：保留上面的本地快照（若有），尽量保持选择器可用
    }
  }
  // 「用户设置 → 知识库检索」开关：持久化到 localStorage，刷新后保持上次选择
  // （此前 ref(false) 每次刷新都重置为关闭，用户会误以为开关不生效）。
  const USE_VECTOR_DB_KEY = 'agent-super:use-vector-db'
  function _readUseVectorDb(): boolean {
    try { return localStorage.getItem(USE_VECTOR_DB_KEY) === '1' }
    catch { return false }
  }
  const useVectorDb = ref(_readUseVectorDb())
  watch(useVectorDb, (v) => {
    try { localStorage.setItem(USE_VECTOR_DB_KEY, v ? '1' : '0') } catch { /* 隐私模式等忽略 */ }
  })
  // 当前/新建会话绑定的工作目录（opencode ctx.directory）。首条消息发送时
  // 随请求 directory 创建会话；已有会话在 loadConversation 时同步为服务器值。
  const sessionDirectory = ref('')
  // Agent 模式选择（对齐 opencode agent modes）：顶层命令只有 default(build)/plan；explore 由委派进入
  const agentMode = ref<'default' | 'plan'>('default')

  // --- 重试机制 ---
  const AUTO_RETRY_DELAY = 5 // 秒
  const MAX_AUTO_RETRIES = 2
  let autoRetryTimer: ReturnType<typeof setTimeout> | null = null
  // [S8] 独立自动重试计数：startAutoRetry 自增，用户主动新发 / 成功 done 清零。
  // 替代旧的「数尾部连续 error 消息」方式——手动重试成功后计数不再残留。
  let retryCount = 0
  // 自动重试重发时置位：send 开头跳过 cancelAutoRetry() 的计数清零，
  // 让 S8 独立计数在连发重试之间持续累加（否则每次重发都被清零、永远达不到上限）。
  let suppressRetryReset = false
  const retryCountdown = ref(0)
  const autoRetrySessionId = ref<string | undefined>(undefined)
  const retryMessageText = ref('')
  // [S6/S7] 发送前校验 / 排队提示的轻量 notice，视图上短暂展示
  const notice = ref('')
  let _noticeTimer: ReturnType<typeof setTimeout> | null = null
  function setNotice(msg: string) {
    notice.value = msg
    if (_noticeTimer) { clearTimeout(_noticeTimer); _noticeTimer = null }
    _noticeTimer = setTimeout(() => { notice.value = '' }, 4000)
  }

  function setSessionDirectory(dir: string) {
    sessionDirectory.value = dir || ''
  }

  function getOrCreateSession(sessionId: string, title?: string): SessionState {
    if (!sessions.value[sessionId]) {
      sessions.value[sessionId] = {
        messages: [],
        conversationId: undefined,
        conversationTitle: title || '',
        loading: false,
        abortController: null,
        streamPhase: 'idle',
        queuePosition: null,
        pendingPrompts: [],
      }
    }
    return sessions.value[sessionId]
  }

  // [S5] 持久化防抖计时器：按会话管理，避免高频写入 IndexedDB。
  // 只有 `schedulePersist` 走防抖；`persistSession`（直接调用）与 `flushPersist`
  // 都会先清掉待执行计时器，保证终态/用户主动操作即时落库、不被过期快照覆盖。
  const _persistTimers: Record<string, ReturnType<typeof setTimeout>> = {}

  function _clearPersistTimer(sessionId: string) {
    const t = _persistTimers[sessionId]
    if (t) { clearTimeout(t); delete _persistTimers[sessionId] }
  }

  // 将会话消息持久化到 IndexedDB（过滤 live 占位；整条覆盖，不残留已删消息）
  async function persistSession(sessionId: string) {
    _clearPersistTimer(sessionId)
    const session = sessions.value[sessionId]
    if (!session) return
    const messages = session.messages.filter(m => !m.live)
    const cacheKey = session.conversationId || sessionId
    await saveSessionToCache<MultiAgentMessage>(
      cacheKey,
      messages,
      session.conversationId,
      session.conversationTitle,
    )
    if (cacheKey !== sessionId) {
      await deleteSessionFromCache(sessionId)
    }
  }

  // [S5] 防抖持久化：1.2s 尾随合并。适用于频率较高但非终态的写入
  // （如发送时的初始 user 消息——随后 done/error 会有终态写入覆盖兜底）。
  function schedulePersist(sessionId: string) {
    _clearPersistTimer(sessionId)
    _persistTimers[sessionId] = setTimeout(() => {
      delete _persistTimers[sessionId]
      persistSession(sessionId)
    }, 1200)
  }

  // [S5] 立即冲刷某会话的防抖写入（页面卸载/删除/撤销等终态操作）。
  async function flushPersist(sessionId: string) {
    _clearPersistTimer(sessionId)
    await persistSession(sessionId)
  }

  function flushAllPendingPersists() {
    for (const sessionId of Object.keys(_persistTimers)) {
      _clearPersistTimer(sessionId)
      persistSession(sessionId)
    }
  }

  // [S5] 卸载/关页前冲刷所有待写入会话，防抖不丢数据
  onScopeDispose(() => flushAllPendingPersists())
  if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', flushAllPendingPersists)
  }

  const currentSession = computed(() => {
    if (!activeSessionId.value) return null
    return sessions.value[activeSessionId.value] || null
  })

  const messages = computed(() => currentSession.value?.messages || [])
  const conversationId = computed(() => currentSession.value?.conversationId)
  const conversationTitle = computed(() => currentSession.value?.conversationTitle)
  const loading = computed(() => currentSession.value?.loading || false)
  const streamPhase = computed(() => currentSession.value?.streamPhase || 'idle')
  const queuePosition = computed(() => currentSession.value?.queuePosition ?? null)
  /** [队列] 当前会话的待处理任务（新→旧执行；已 drain 的会从这里移除） */
  const pendingPrompts = computed(() => currentSession.value?.pendingPrompts ?? [])

  async function loadConversations() {
    loadModels()
    try { conversations.value = await listConversations('multi-agent') }
    catch (e) { console.error('Failed to load conversations:', e) }
  }

  async function loadConversation(id: string) {
    const meta = conversations.value.find(c => c.id === id)
    const session = getOrCreateSession(id, meta?.title)
    session.conversationId = id
    if (meta) session.conversationTitle = meta.title
    // 会话正在流式 → 保留本地消息，不覆盖
    if (session.loading || session.streamPhase !== 'idle') { activeSessionId.value = id; return }
    try {
      const detail = await getConversation(id, 'multi-agent')
      session.conversationTitle = detail.title
      // 同步会话绑定目录（服务器为准；无论空与非空都覆盖，避免残留上一会话的目录）
      sessionDirectory.value = detail.directory || ''
      // [模型切换] 打开会话时同步为其模型（opencode 会话默认模型语义）；目录里没有则保持当前选择
      if (detail.model?.id && modelOptions.value.some(m => m.value === detail.model!.id)) {
        selectedModel.value = detail.model!.id
      }
      const serverMessages: MultiAgentMessage[] = detail.messages.map(m => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content || '',
        agents: (m as any).agents || [],
        files: (m as any).files || [],
        voice: (m as any).voice,
        model: (m as any).model,
        tokens: (m as any).tokens,
        cost: (m as any).cost,
        files_changed: (m as any).files_changed || [],
        snapshotRestored: !!((m as any).snapshot_restored),
        is_compacted: !!((m as any).is_compacted),
        // [C8] 计划文件路径（plan Agent 落盘产物）——历史回放也要能看到「查看计划」入口
        plan_path: (m as any).plan_path || undefined,
        partial_error: (m as any).partial_error ?? null,
        timestamp: new Date(),
      }))
      // [#11 双真相源] 在线时服务端是唯一真相源：后端在回合开始即落库 user 消息、
      // done/断连兜底落库 assistant，刷新直接取服务器消息，不再与本地缓存合并。
      // 用服务器消息**整条覆盖**缓存（离线回退也以它为准）。
      session.messages = serverMessages
      await saveSessionToCache(id, serverMessages, id, detail.title)
      activeSessionId.value = id
    } catch (e) {
      console.error('Failed to load conversation:', e)
      // 服务器获取失败（离线），退化为只读缓存
      const cached = await loadSessionFromCache<MultiAgentMessage>(id)
      if (cached) {
        session.messages = cached.messages.filter(m => !m.live)
        session.conversationTitle = cached.conversationTitle || session.conversationTitle
      }
      activeSessionId.value = id
    }
  }

  async function renameConversation(id: string, title: string) {
    try {
      await apiRenameConversation(id, title)
      const session = sessions.value[id]
      if (session) session.conversationTitle = title
      const idx = conversations.value.findIndex(c => c.id === id)
      if (idx >= 0) conversations.value[idx].title = title
    } catch (e) { console.error('Failed to rename:', e) }
  }

  // --- 会话运维操作（D1/D2：接入此前无人调用的 fork / compact / status 路由）---

  /** 分叉会话：可选 `messageId` 作为分叉点（截断到该条），返回新会话 id。 */
  async function forkConversation(id: string, messageId?: string): Promise<string | null> {
    try {
      const forked = await apiForkSession(id, messageId)
      await loadConversations()
      return forked.id
    } catch (e) {
      console.error('Failed to fork conversation:', e)
      notice.value = '分叉会话失败'
      return null
    }
  }

  /** 手动压缩上下文（落 compaction checkpoint + tail 快照），完成后重载消息。 */
  async function compactConversation(id: string): Promise<boolean> {
    const serverId = sessions.value[id]?.conversationId || id
    try {
      await apiCompactSession(serverId)
      await loadConversations()
      notice.value = '已压缩上下文'
      return true
    } catch (e) {
      console.error('Failed to compact conversation:', e)
      notice.value = '压缩失败'
      return false
    }
  }

  /** 查询后端会话状态（idle/running/…）——列表徽标与服务端对齐的依据。 */
  async function fetchSessionStatus(id: string): Promise<SessionStatus | null> {
    const serverId = sessions.value[id]?.conversationId || id
    try {
      return await apiGetSessionStatus(serverId)
    } catch (e) {
      console.error('Failed to fetch session status:', e)
      return null
    }
  }

  function newChat(dir?: string) {
    routingStatus.value = ''
    activeSessionId.value = undefined
    if (dir !== undefined) {
      sessionDirectory.value = dir
    } else {
      // 自动使用设置里的默认工作目录（第一个工作区）
      const perm = usePermissionStore()
      sessionDirectory.value = perm.workspaces.length > 0 ? perm.workspaces[0] : ''
    }
  }

  // --- 错误分类 ---

  function classifySSEError(event: MultiAgentSSEEvent): ChatError {
    const detail = event.error || event.detail || 'Unknown error'
    if (event.retryable) {
      if (event.status_code === 429) return { type: 'rate_limit', message: detail, retryable: true, statusCode: 429 }
      if (event.status_code === 503) return { type: 'server_error', message: detail, retryable: true, statusCode: 503 }
      if (event.status_code && event.status_code >= 500) return { type: 'server_error', message: detail, retryable: true, statusCode: event.status_code }
      return { type: 'network', message: detail, retryable: true }
    }
    return { type: 'unknown', message: detail, retryable: false }
  }

  function formatErrorMessage(info: ChatError): string {
    switch (info.type) {
      case 'rate_limit': return `请求过于频繁（${info.statusCode || 429}），请稍后重试`
      case 'server_error': return `服务器错误（${info.statusCode || 500}），请稍后重试`
      case 'network': return `网络连接中断，请检查网络后重试`
      case 'timeout': return `请求超时，请稍后重试`
      default: return `出错了: ${info.message}`
    }
  }

  // --- 自动重试 ---

  function startAutoRetry(sessionId: string, text: string) {
    const session = sessions.value[sessionId]
    if (!session) return

    // [S8] 独立重试计数：达到上限则放弃（cancelAutoRetry 会清计数，属终止路径）
    if (retryCount >= MAX_AUTO_RETRIES) {
      cancelAutoRetry()
      return
    }

    // 清掉上个 timer（只清 timer，不清计数——不能走 cancelAutoRetry）
    if (autoRetryTimer) {
      clearInterval(autoRetryTimer)
      autoRetryTimer = null
    }
    retryCount++
    retryCountdown.value = AUTO_RETRY_DELAY
    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    autoRetryTimer = setInterval(() => {
      retryCountdown.value--
      if (retryCountdown.value <= 0) {
        if (autoRetryTimer) {
          clearInterval(autoRetryTimer)
          autoRetryTimer = null
        }
        retryLastMessage()
      }
    }, 1000)
  }

  function cancelAutoRetry() {
    if (autoRetryTimer) {
      clearInterval(autoRetryTimer)
      autoRetryTimer = null
    }
    retryCountdown.value = 0
    autoRetrySessionId.value = undefined
    retryMessageText.value = ''
    // [S8] 取消/成功/主动新发一律清零独立重试计数
    retryCount = 0
  }

  // 重试最后一条用户消息
  async function retryLastMessage() {
    const sessionId = autoRetrySessionId.value || activeSessionId.value
    if (!sessionId) return
    const session = sessions.value[sessionId]
    if (!session) return

    const lastUserMsg = [...session.messages].reverse().find(m => m.role === 'user')
    if (!lastUserMsg) return

    // 删除最后一条 error 消息（如果有）
    const lastMsg = session.messages[session.messages.length - 1]
    if (lastMsg?.isError) {
      session.messages = session.messages.slice(0, -1)
    }

    // 同时移除原 user 消息，避免重试后产生重复的用户消息
    const lastUserMsgIdx = session.messages.findIndex(m => m.id === lastUserMsg.id)
    if (lastUserMsgIdx >= 0) {
      session.messages = session.messages.slice(0, lastUserMsgIdx)
    }

    // [S2] 重试复用原 user 消息的 client_msg_id，服务端按幂等键去重，不产生重复轮次
    // [S8] 置位抑制 send 开头的计数清零，让独立重试计数在连发间持续累加
    suppressRetryReset = true
    // [F8] 重试复用原附件
    await send(lastUserMsg.content, lastUserMsg.clientMsgId, lastUserMsg.files, lastUserMsg.voice)
  }

  // 手动重试（从 error 消息的 UI 触发）
  async function manualRetry(messageId: string) {
    if (!activeSessionId.value) return
    const session = sessions.value[activeSessionId.value]
    if (!session) return

    cancelAutoRetry()

    const errorIdx = session.messages.findIndex(m => m.id === messageId)
    if (errorIdx < 0) return

    let userIdx = -1
    for (let i = errorIdx - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') {
        userIdx = i
        break
      }
    }
    if (userIdx < 0) return

    const userMsg = session.messages[userIdx]
    session.messages = session.messages.slice(0, userIdx)
    // [S2] 手动重试同样复用原 user 消息的 client_msg_id，服务端幂等去重
    // [F8] 重试复用原附件
    await send(userMsg.content, userMsg.clientMsgId, userMsg.files, userMsg.voice)
  }

  // 把内存会话的 key 从客户端 genId 迁移到服务器 conversation_id。
  // 只能成功完成（done）或失败（error/断连）后调用：一旦知道服务器 id，
  // 就将内存里的完整消息（含流式中断的部分内容）挂到服务器 id 下，
  // 避免之后 loadConversation(serverId) 因找不到内存会话而新建空会话，
  // 导致「断连后重连内容丢失、要重新访问后台」。
  function migrateToServerId(sessionId: string) {
    const session = sessions.value[sessionId]
    if (!session) return
    const serverId = session.conversationId
    if (!serverId || serverId === sessionId) return
    if (!sessions.value[serverId]) {
      sessions.value[serverId] = session
    } else if (sessions.value[serverId] !== session) {
      // 已存在不同对象：把当前消息合并进已存在会话（保留内容最多的）
      const target = sessions.value[serverId]
      if (session.messages.length > target.messages.length) {
        target.messages = session.messages
        target.conversationTitle = session.conversationTitle
      }
    }
    delete sessions.value[sessionId]
    if (activeSessionId.value === sessionId) activeSessionId.value = serverId
    if (autoRetrySessionId.value === sessionId) autoRetrySessionId.value = serverId
  }

  // [队列] 回合收尾：把权威答案/用量/改动/计划文件回填到该回合的助手消息。
  // done 与 turn_done 共用（多回合 drain 时每个回合都要走一次）。
  function finalizeTurn(
    session: SessionState,
    event: MultiAgentSSEEvent,
    msg: MultiAgentMessage,
    map: Record<string, AgentStreamData>,
  ): void {
    if (event.title) { session.conversationTitle = event.title; loadConversations() }
    if (event.answer) msg.content = event.answer
    else if (event.content) msg.content = event.content
    // [模型目录] 回填本轮 model/token 用量/成本
    if (event.model) msg.model = event.model
    if (event.tokens) msg.tokens = event.tokens
    if (typeof event.cost === 'number' && event.cost > 0) msg.cost = event.cost
    // [文件改动] 本轮改动的文件 + 行数（快照 diff）
    if (event.files_changed && event.files_changed.length) msg.files_changed = event.files_changed
    // [plan→build] 计划已成但执行出错：正文（含「## 实施计划」）必须保留，
    // 追加一行出错提示并标记 isError。走 error 事件会把正文覆盖成错误文案。
    if (event.partial_error) {
      msg.isError = true
      msg.partial_error = event.partial_error
      msg.content = `${msg.content}\n\n> ⚠ 执行出错：${event.partial_error}`
    }
    // [C8] plan Agent 落盘的计划文件（前端展示「查看计划」入口，可复制路径）
    if (event.plan_path) msg.plan_path = event.plan_path
    // 回填服务器生成的消息 id，保证删除/撤销能命中真实消息
    if (event.assistant_msg_id) msg.id = event.assistant_msg_id
    if (event.user_msg_id) {
      for (let i = session.messages.length - 1; i >= 0; i--) {
        const m = session.messages[i]
        if (m.role === 'user') {
          if (m.id !== event.user_msg_id) m.id = event.user_msg_id
          break
        }
      }
    }
    // 兜底：直播事件缺失时用后端快照回填 agent 面板（如重连/丢事件）
    if (Object.keys(map).length === 0 && event.agents?.length) {
      msg.agents = event.agents
    } else {
      msg.agents = Object.values(map)
    }
  }

  async function send(text: string, clientMsgId?: string, files?: FileContent[], voice?: VoiceMessageData): Promise<boolean> {
    let sessionId = activeSessionId.value
    if (!sessionId) {
      sessionId = genId()
      getOrCreateSession(sessionId)
      activeSessionId.value = sessionId
    }

    const session = sessions.value[sessionId]
    if (!session) return false
    // [队列] 执行中（或本会话还有待处理任务）再次发送 → 不再拒绝，改为「追加排队任务」
    // （对齐 opencode：执行中仍可发消息；顺序 = 新→旧 LIFO，后端当前轮结束后 drain）。
    // pendingPrompts 用 ?. 兜底：历史缓存/测试里手工构造的 session 对象可能没有该字段。
    if (session.loading || session.streamPhase !== 'idle' || (session.pendingPrompts?.length ?? 0) > 0) {
      return enqueueWhileBusy(sessionId, session, text, files, voice)
    }
    // [S6] 发送前校验模型/目录有效性，避免带无效 model 触发后端 404
    if (!modelOptions.value.some(m => m.value === selectedModel.value)) {
      setNotice(`当前模型不可用：${selectedModel.value}，请重新选择`)
      return false
    }
    if (suppressRetryReset) {
      // [S8] 自动重试重发：只清 timer，保留独立重试计数（在连发间持续累加）
      if (autoRetryTimer) {
        clearInterval(autoRetryTimer)
        autoRetryTimer = null
      }
      retryCountdown.value = 0
      suppressRetryReset = false
    } else {
      // 新的/手动发送：完整取消自动重试（含清零独立重试计数）
      cancelAutoRetry()
    }

    let completed = false

    // [S2] 幂等 id：重试时复用原 user 消息的 clientMsgId，首次发送则新生成
    const messageClientId = clientMsgId ?? genId()
    const userMsg: MultiAgentMessage = {
      id: genId(), role: 'user', content: text, agents: [], timestamp: new Date(),
      clientMsgId: messageClientId,
      // [F8] 附件随消息落库，回显与重试时按原样重发
      files: files && files.length ? files : undefined,
      // [语音消息] 音频气泡随消息落库，回显与重试时按原样重发
      voice: voice || undefined,
    }
    session.messages = [...session.messages, userMsg]
    session.loading = true
    session.streamPhase = 'queued'
    session.queuePosition = null

    // 首条消息立即用内容作标题（与后端 `body.message[:20]` 口径一致），
    // 顶部标题 / 左侧历史即时可见，无需等 done 事件回传 title。
    if (!session.conversationTitle) session.conversationTitle = text.slice(0, 20)

    const assistantMsgId = genId()
    let agentsMap: Record<string, AgentStreamData> = {}
    let assistantMsg: MultiAgentMessage = reactive({
      id: assistantMsgId, role: 'assistant', content: '', agents: [], timestamp: new Date(),
    })
    session.messages = [...session.messages, assistantMsg]

    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    // 发送后立即持久化（SSE 中断也不丢失 user 消息）
    // [S5] 走防抖（非终态写，done/error 的终态 persistSession 会清 timer 兜底覆盖）
    schedulePersist(sessionId)

    const reqData = { message: text, conversation_id: session.conversationId, model: selectedModel.value, use_vector_db: useVectorDb.value, directory: sessionDirectory.value || undefined, client_msg_id: messageClientId, files: files && files.length ? files : undefined, voice: voice || undefined, agent_mode: agentMode.value !== 'default' ? agentMode.value : undefined }
    const controller = new AbortController()
    session.abortController = controller
    const signal = controller.signal

    try {
      const harness = useHarnessTurn()
      await harness.run(reqData, {
        signal,
        onSessionId: (sid) => {
          if (!session.conversationId) session.conversationId = sid
        },
        onEvent: (event, turnState) => {
        if (signal.aborted) return

          const reduced = turnState

        if (
          event.type === 'agent_start' ||
          event.type === 'agent_step' ||
          event.type === 'agent_done' ||
          event.type === 'agent_error' ||
          event.type === 'text_delta'
        ) {
          if (event.type === 'agent_start') routingStatus.value = ''
          agentsMap = reduced.agents
          assistantMsg.agents = Object.values(agentsMap)
          if (event.type === 'text_delta') {
            assistantMsg.content = reduced.assistantContent
          }
          return
        }

        // 尽早记录服务器会话 id（后端在首个事件即注入），保证新会话也能被"停止"打断
        if (event.conversation_id && !session.conversationId) {
          session.conversationId = event.conversation_id
        }

        if (event.type === 'queued') {
          session.streamPhase = 'queued'
          session.queuePosition = event.queue_position ?? null
          return
        }

        // [model_switched] 会话模型被切换。必须排在下面的 running 转换**之前**：
        // 后端在队列检查之前就发这个事件，若在此处落到转换逻辑会先把
        // streamPhase 置为 running 并清空 queuePosition，随后 queued 事件再改回来 →
        // 排队请求的表头闪一下。这里提前 return 即可（同时消掉该事件此前的静默丢弃）。
        if (event.type === 'model_switched') {
          const ref = event.model_ref
          if (ref?.id) {
            assistantMsg.model = ref.name || ref.id
          }
          return
        }

        // 收到任何执行事件 → 运行阶段
        if (session.streamPhase !== 'running') {
          session.streamPhase = 'running'
          session.queuePosition = null
        }

        if (event.type === 'routing') {
          routingStatus.value = event.detail || 'Routing...'
        } else if (event.type === 'permission_request') {
          const permStore = usePermissionStore()
          permStore.handleIncoming({
            id: event.request_id!,
            path: event.path!,
            operation: event.operation!,
            tool_name: event.tool_name!,
            tool_args: event.tool_args as Record<string, unknown>,
            created_at: event.created_at || new Date().toISOString(),
            expires_at: event.expires_at,
          })
        } else if (event.type === 'error') {
          session.streamPhase = 'idle'
          routingStatus.value = ''
          assistantMsg.isError = true
          const errorInfo = classifySSEError(event)
          assistantMsg.errorInfo = errorInfo
          assistantMsg.content = formatErrorMessage(errorInfo)
          persistSession(sessionId)
          // 自动重试：仅对可重试错误且未超过最大次数
          if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
            startAutoRetry(sessionId, text)
          }
        } else if (event.type === 'done' || event.type === 'turn_done') {
          // [队列] turn_done = 本回合完成但还有排队任务（同一条 SSE 流继续跑下一个回合）；
          // done = 队列已空，断流收尾。两者共用同一套「回合收尾」逻辑。
          const moreComing = event.type === 'turn_done'
          finalizeTurn(session, event, assistantMsg, agentsMap)
          if (!moreComing) {
            completed = true
            session.streamPhase = 'idle'
            routingStatus.value = ''
            session.conversationId = event.conversation_id || session.conversationId
            persistSession(sessionId)
            migrateToServerId(sessionId)
            cancelAutoRetry()
            return
          }
          // 还有排队任务：把「排队中」的气泡转为已回答，并新建一个助手占位接下一个回合
          for (const m of session.messages) {
            if (m.role === 'user' && m.queued) {
              m.queued = false
              const pid = m.queuePromptId || m.clientMsgId
              if (pid) {
                session.pendingPrompts = (session.pendingPrompts ?? []).filter(p => p.id !== pid)
                m.queuePromptId = undefined
              }
            }
          }
          session.streamPhase = 'running'
          agentsMap = {}
          assistantMsg = reactive({
            id: genId(), role: 'assistant', content: '', agents: [], timestamp: new Date(),
          })
          session.messages = [...session.messages, assistantMsg]
          persistSession(sessionId)
        } else if (event.type === 'queue_drain') {
          // [队列] 纯提示：当前轮之后还有 N 个排队任务（实际 drain 由 turn_done 驱动）
          if (event.queue_size != null) {
            session.queuePosition = event.queue_size
          }
        }
        },
      })
    } catch (err: any) {
      session.streamPhase = 'idle'
      session.queuePosition = null
      const isChatError = err && typeof err === 'object' && 'retryable' in err
      const errorInfo: ChatError = isChatError ? err as ChatError : classifyNetworkError(err)
      if (!signal.aborted) {
        assistantMsg.isError = true; assistantMsg.errorInfo = errorInfo; assistantMsg.content = formatErrorMessage(errorInfo)
        assistantMsg.agents = Object.values(agentsMap)
        persistSession(sessionId)
        // 失败/断连后也已拿到服务器 id：立刻迁移内存 key，避免后续
        // loadConversation(serverId) 新建空会话把内容顶掉
        migrateToServerId(sessionId)
        // 自动重试：仅对可重试错误且未超过最大次数
        if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
          startAutoRetry(sessionId, text)
        }
      }
    } finally {
      routingStatus.value = ''
      session.loading = false
      session.streamPhase = 'idle'
      session.queuePosition = null
      if (session.abortController === controller) session.abortController = null
      // [终态兜底] 队列 drain 在 turn_done 后断流（无后续 done）时，会残留一个空的
      // 助手占位（turn_done 分支新建来接下个回合）。未收到终态 done 且该占位仍为空
      // → 移除它，避免聊天框里挂一条永远「思考中」的空气泡。
      if (!completed) {
        const tail = session.messages[session.messages.length - 1]
        if (
          tail && tail.role === 'assistant' && !tail.isError &&
          !tail.content && !(tail.agents && tail.agents.length) &&
          !tail.files_changed?.length
        ) {
          session.messages = session.messages.slice(0, -1)
          persistSession(sessionId)
        }
      }
    }
    return completed
  }

  // [队列] 执行中追加任务：落一条「排队中」用户消息 + POST /prompts。
  // 后端在当前轮结束时按新→旧 drain；这里不建 SSE 流（复用正在跑的那条）。
  async function enqueueWhileBusy(
    sessionId: string,
    session: SessionState,
    text: string,
    files?: FileContent[],
    voice?: VoiceMessageData,
  ): Promise<boolean> {
    if (!session.conversationId) {
      // 首条消息还在建会话（服务器 id 未回填）→ 无法入队，保持原提示
      setNotice('上一条消息仍在处理中，请稍候再发送')
      return false
    }
    if (!text.trim() && !(files && files.length) && !voice) return false

    const messageClientId = genId()
    const userMsg: MultiAgentMessage = {
      id: genId(), role: 'user', content: text, agents: [], timestamp: new Date(),
      clientMsgId: messageClientId,
      queued: true,
      files: files && files.length ? files : undefined,
      voice: voice || undefined,
    }
    session.messages = [...session.messages, userMsg]

    try {
      const row = await enqueueSessionPrompt(
        session.conversationId,
        {
          message: text,
          files: files && files.length ? files : undefined,
          voice: voice || undefined,
          agent_mode: agentMode.value !== 'default' ? agentMode.value : undefined,
        },
        messageClientId,
      )
      userMsg.queuePromptId = row?.id || messageClientId
      session.pendingPrompts = [...(session.pendingPrompts ?? []), { id: userMsg.queuePromptId, message: text }]
      await persistSession(sessionId)
      return true
    } catch (e) {
      // 入队失败（会话已被删除/无权/网络）：撤掉刚插入的气泡，不留假排队项
      session.messages = session.messages.filter(m => m.id !== userMsg.id)
      setNotice(`排队失败：${e instanceof Error ? e.message : String(e)}`)
      await persistSession(sessionId)
      return false
    }
  }

  /** [队列] 移除一条待处理任务（用户后悔了 / 想插队到最前） */
  async function removeQueuedPrompt(sessionId: string, promptId: string): Promise<void> {
    const session = sessions.value[sessionId]
    if (!session) return
    if (session.conversationId) {
      try {
        await removeSessionPrompt(session.conversationId, promptId)
      } catch (e) {
        // 后端已 drain 或已删除 → 仍按本地移除（幂等），仅记录
        console.warn('移除排队任务失败（已按本地移除）:', e)
      }
    }
    session.pendingPrompts = (session.pendingPrompts ?? []).filter(p => p.id !== promptId)
    session.messages = session.messages.filter(
      m => !(m.role === 'user' && (m.queuePromptId === promptId || m.clientMsgId === promptId)),
    )
    await persistSession(sessionId)
  }

  async function cancel() {
    if (activeSessionId.value) {
      const session = sessions.value[activeSessionId.value]
      if (session) {
        // 本地中断 SSE 连接（前端立即停止接收）
        session.abortController?.abort()
        // 通知后端真正停止 Agent 任务（fire-and-forget，本地 abort 不会让后端停止）
        if (session.conversationId) {
          interruptSession(session.conversationId).catch((e) => {
            console.error('Failed to interrupt session:', e)
          })
        }
      }
    }
  }

  function clear() {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s) { s.messages = []; s.conversationId = undefined; s.conversationTitle = '' }
      deleteSessionFromCache(activeSessionId.value)
    }
  }

  // 撤销到指定索引：保留 [0, index)，删除其后所有消息（走 /sessions/{id}/revert）
  async function undoMessage(index: number) {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (!s) return
      // [revert 竞态] 若正在生成，先停止在途流（本地 abort + 后端 interrupt）再撤销：
      // 否则撤销后 SSE 事件继续回写 streamPhase，对话框永远卡在「思考生成回答」，
      // 且后端 finally 的兜底落库会往已撤销的会话写回内容。
      if (s.streamPhase !== 'idle' || s.abortController) {
        s.abortController?.abort()
        s.abortController = null
        if (s.conversationId) {
          try { await interruptSession(s.conversationId) } catch (e) { console.error('Failed to interrupt before undo:', e) }
        }
        if (autoRetrySessionId.value === activeSessionId.value) cancelAutoRetry()
        s.streamPhase = 'idle'
        s.queuePosition = null
        s.loading = false
      }
      // 目标保留消息 = 撤销点前一条；index===0 无保留消息，仅本地清空
      const target = index > 0 ? s.messages[index - 1] : undefined
      if (s.conversationId && target) {
        try { await revertSession(s.conversationId, target.id) } catch (e) { console.error('Failed to sync undo:', e) }
      }
      // 以服务器 revert 为准，本地同步裁剪；persistSession 整条覆盖缓存，无需墓碑
      s.messages = s.messages.slice(0, index)
      await persistSession(activeSessionId.value)
    }
  }

  // [查看改动] 拉取某条 assistant 消息对应轮次的 diff 文本（展开文件改动时按需加载）。
  // 只返回数据，不写回 message：缓存由 DiffViewer 自管（按 messageId+step 命名空间），
  // 避免把 UI 缓存字段塞进消息模型。
  // step 省略 = 整轮净 diff；传 step=N = 只看第 N 步（每 step 快照）。
  async function fetchMessageDiff(messageId: string, step?: number) {
    const s = activeSessionId.value ? sessions.value[activeSessionId.value] : undefined
    if (!s?.conversationId) throw new Error('会话尚未在服务器创建，无法查看改动')
    return apiFetchTurnDiff(s.conversationId, messageId, '', step)
  }

  // [撤回改动] 恢复某条 assistant 消息对应轮次的文件改动（调后端 restore-snapshot）
  async function restoreSnapshot(messageId: string) {
    const s = activeSessionId.value ? sessions.value[activeSessionId.value] : undefined
    if (!s?.conversationId) throw new Error('会话尚未在服务器创建，无法撤回改动')
    const result = await apiRestoreSnapshot(s.conversationId, messageId)
    const msg = s.messages.find(m => m.id === messageId)
    if (msg) {
      // DiffViewer 监听 restored 变化自行清空 diff 缓存，消息模型不再持有 UI 缓存。
      msg.snapshotRestored = true
    }
    await persistSession(activeSessionId.value!)
    return result
  }

  // 删除单条消息：先同步后端（存在会话时），再移除本地
  async function deleteMessage(messageId: string) {
    if (activeSessionId.value) {
      const s = sessions.value[activeSessionId.value]
      if (s?.conversationId) {
        try {
          await deleteSessionMessage(s.conversationId, messageId)
        } catch (e) {
          console.error('Failed to delete message from server:', e)
        }
      }
      if (s) {
        s.messages = s.messages.filter(m => m.id !== messageId)
        await persistSession(activeSessionId.value)
      }
    }
  }

  async function deleteConversation() {
    if (activeSessionId.value) {
      cancelAutoRetry()
      const s = sessions.value[activeSessionId.value]
      s?.abortController?.abort()
      const serverId = s?.conversationId || activeSessionId.value
      if (serverId) { try { await apiDeleteConversation(serverId) } catch (e) { console.error('Failed to delete:', e) } }
      await deleteSessionFromCache(serverId)
      if (serverId !== activeSessionId.value) await deleteSessionFromCache(activeSessionId.value)
      delete sessions.value[activeSessionId.value]
      activeSessionId.value = undefined
      loadConversations()
    }
  }

  // [模型切换] 选择器变更 → PATCH 会话默认模型（opencode switchModel 持久化语义）。
  // 已存在会话必须等服务端门禁/迁移成功后再改 UI；失败保留旧模型并提示。
  async function persistSelectedModel(target?: string): Promise<boolean> {
    const model = target || selectedModel.value
    // 参考 opencode FAST_BOOT：provider 目录未就绪前不 set，失败显式报错。
    await loadModels(true)
    if (!modelOptions.value.some(o => o.value === model)) {
      setNotice(`模型目录未就绪或模型不存在：${model}`)
      return false
    }
    const s = activeSessionId.value ? sessions.value[activeSessionId.value] : undefined
    // 新会话尚未落库（只有本地 id）：只改本地选择，等服务端创建会话时再继承。
    if (!s || !s.conversationId) {
      selectedModel.value = model
      return true
    }
    const sessionId = s.conversationId
    const previous = selectedModel.value
    const before = new Map(s.messages.map(m => [m.id, m]))
    const beforeIds = [...before.keys()]
    try {
      setNotice('正在切换模型，历史加载中…')
      const providerID = model.split('/')[0]
      await apiUpdateSession(sessionId, { model: { id: model, providerID } })
      // §3.1 消息完整性：切换后从服务端核对，未压缩消息不得丢失。
      try {
        const detail = await getConversation(sessionId, 'multi-agent')
        const afterIds = new Set(detail.messages.map(m => m.id))
        const afterContents = new Set(detail.messages.map(m => m.content))
        const lost = beforeIds.filter(id => {
          const old = before.get(id) as any
          if (!old || old.is_compacted) return false
          return !afterIds.has(id) && !afterContents.has(old.content)
        })
        if (lost.length) {
          await apiUpdateSession(sessionId, {
            model: { id: previous, providerID: previous.split('/')[0] },
          })
          setNotice('消息丢失，已回滚模型切换')
          return false
        }
      } catch {
        // 历史拉取失败不阻断切换；本地列表原地保留。
      }
      selectedModel.value = model
      setNotice('')
      await loadConversations()
      return true
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setNotice(`切换模型失败：${msg}`)
      return false
    }
  }

  return {
    sessions, activeSessionId, conversations, routingStatus, selectedModel, useVectorDb,
    models, modelOptions, defaultModelId, smallModelId, smallModelInfo, loadModels, persistSelectedModel,
    imageCaptionModelId, imageCaptionModelInfo, voiceModelSize,
    sessionDirectory, setSessionDirectory, agentMode,
    messages, conversationId, conversationTitle, loading, streamPhase, queuePosition,
    pendingPrompts, removeQueuedPrompt,
    retryCountdown, notice, setNotice,
    send, cancel, clear, undoMessage, restoreSnapshot, fetchMessageDiff, deleteMessage, deleteConversation,
    loadConversations, loadConversation, newChat, renameConversation,
    forkConversation, compactConversation, fetchSessionStatus,
    retryLastMessage, manualRetry, cancelAutoRetry,
  }
})
