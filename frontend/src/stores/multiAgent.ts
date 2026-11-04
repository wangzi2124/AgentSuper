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
  interruptSession, revertSession, deleteSessionMessage,
  forkSession as apiForkSession, compactSession as apiCompactSession, getSessionStatus as apiGetSessionStatus,
  type SessionStatus,
} from '../api/sessions'
import { loadModelCache, saveModelCache } from '../api/model-cache'
import { classifyNetworkError } from '../api/errors'
import { useHarnessTurn } from '../harness/useHarnessTurn'
import { usePermissionStore } from './permission'
import { useSessionModelStore, SESSION_MODEL_DRAFT_KEY } from './sessionModel'
import { useModelSwitchStore } from './modelSwitch'

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
  /** [后台续流] 当前订阅中的服务端回合（断连重连锁定只接这一轮） */
  attachedTurnId?: string
}

export const useMultiAgentStore = defineStore('multiAgent', () => {
  const sessions = ref<Record<string, SessionState>>({})
  const activeSessionId = ref<string | undefined>(undefined)
  const conversations = ref<ConversationMeta[]>([])
  const routingStatus = ref<string>('')
  // [模型目录] 选择器由后端 GET /api/models 动态驱动；SUPPORTED_MODELS 仅为离线兜底。
  const models = ref<ModelInfo[]>([])
  const defaultModelId = ref<string>('')
  const sessionModel = useSessionModelStore()
  const modelSwitch = useModelSwitchStore()
  const sessionModelKey = computed(() => activeSessionId.value || SESSION_MODEL_DRAFT_KEY)
  // 选择先写 desired；空闲会话后台 PATCH，发送前 wait 复用结果。
  const selectedModel = computed<string>({
    get: () => sessionModel.desired(sessionModelKey.value)
      || sessionModel.active(sessionModelKey.value)
      || defaultModelId.value
      || SUPPORTED_MODELS[0].value,
    set: (value: string) => sessionModel.select(sessionModelKey.value, value),
  })
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
  // 会话工作目录不可由前端指定：根固定为后端隔离的默认临时目录；
  // 额外免审目录统一在「用户设置 → 工作目录白名单」维护。
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
        attachedTurnId: undefined,
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

  async function loadConversation(id: string, autoResume = true) {
    const meta = conversations.value.find(c => c.id === id)
    const session = getOrCreateSession(id, meta?.title)
    session.conversationId = id
    if (meta) session.conversationTitle = meta.title
    // 会话正在流式 → 保留本地消息，不覆盖
    if (session.loading || session.streamPhase !== 'idle') { activeSessionId.value = id; return }
    try {
      const detail = await getConversation(id, 'multi-agent')
      session.conversationTitle = detail.title
      // [模型切换] 服务端模型只作为 active 兜底；本地 desired 优先（opencode 按会话偏好）。
      if (detail.model?.id) {
        sessionModel.setActive(id, detail.model.id)
        const local = sessionModel.desired(id)
        if (local && modelOptions.value.length && !modelOptions.value.some(m => m.value === local)) {
          sessionModel.revert(id)
        }
      }
      const serverMessages: MultiAgentMessage[] = detail.messages.map(m => ({
        id: m.id,
        role: m.role as 'user' | 'assistant' | 'system',
        content: m.content || '',
        compaction: ((m as any).data?.compaction as MultiAgentMessage['compaction']) || undefined,
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
        // [错误持久化] 后端把失败回合落库为带 error_info 的 assistant 消息，
        // 刷新/切换会话后错误气泡仍然可见。
        isError: !!((m as any).is_error),
        errorInfo: (m as any).error_info || undefined,
        timestamp: new Date(),
      }))
      // [#11 双真相源] 在线时服务端是唯一真相源：后端在回合开始即落库 user 消息、
      // done/断连兜底落库 assistant，刷新直接取服务器消息，不再与本地缓存合并。
      // 用服务器消息**整条覆盖**缓存（离线回退也以它为准）。
      session.messages = serverMessages
      await saveSessionToCache(id, serverMessages, id, detail.title)
      activeSessionId.value = id
      // [后台续流] 刷新/切回时后台可能仍在跑：有活动回合就自动接上，
      // 断连不再等于取消。
      if (autoResume) void resumeSession(id)
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

  function newChat() {
    routingStatus.value = ''
    const current = selectedModel.value
    activeSessionId.value = undefined
    sessionModel.select(SESSION_MODEL_DRAFT_KEY, current)
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
    sessionModel.copy(sessionId, serverId)
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
    // 本轮实际耗时：从占位消息创建到收尾的墙钟时间。
    // 之前用「工具步骤耗时之和」并在 0 时兜底为 1s，导致纯文本回合恒显示 1s。
    if (typeof msg.startedAt === 'number') {
      msg.duration_ms = Math.max(0, Date.now() - msg.startedAt)
      delete msg.startedAt
    }
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
      // 'system' 是本轮临时提示（如 reconnecting），不随消息持久化展示。
      msg.agents = Object.values(map).filter(a => a.agent_id !== 'system')
    }
  }

  // ── [后台续流] 断连/刷新后重连在途回合 ────────────────────────────────
  function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms))
  }

  function lastUserText(session: SessionState): string {
    for (let i = session.messages.length - 1; i >= 0; i--) {
      const m = session.messages[i]
      if (m.role === 'user') return m.content || ''
    }
    return ''
  }

  function ensureResumePlaceholder(
    session: SessionState,
    turnId: string,
    seq: number,
  ): MultiAgentMessage {
    const id = `local-${turnId}-${seq}`
    const found = session.messages.find(m => m.id === id)
    if (found) return found
    const msg: MultiAgentMessage = reactive({
      id,
      role: 'assistant',
      content: '',
      agents: [],
      timestamp: new Date(),
      startedAt: Date.now(),
    })
    session.messages = [...session.messages, msg]
    return msg
  }

  function removeIfEmptyPlaceholder(session: SessionState, msg: MultiAgentMessage): void {
    if (msg.content || msg.isError) return
    if (msg.agents && msg.agents.length) return
    if (msg.files_changed?.length) return
    session.messages = session.messages.filter(m => m.id !== msg.id)
  }

  /**
   * 订阅一个服务端在途回合：从头重放事件日志重建状态；中途断流先查
   * `/status`，仍在跑就再次续接，只有确认无活且无终态才退回报错/重试。
   * 本地 abort（离开页面/切换会话）只解绑，不取消后台任务。
   */
  async function attachTurn(
    sessionKey: string,
    session: SessionState,
    target: { turn_id: string; last_seq?: number },
  ): Promise<void> {
    const streamId = session.conversationId || sessionKey
    session.attachedTurnId = target.turn_id
    session.loading = true
    session.streamPhase = 'running'
    session.queuePosition = null
    session.abortController?.abort()
    const controller = new AbortController()
    session.abortController = controller

    const resumeNotice: AgentStreamData = {
      agent_id: 'system',
      agent_name: 'System',
      status: 'completed',
      content: '',
      steps: [],
      parts: [{ seq: 0, kind: 'notice', text: '已重新连接，任务在后台继续' }],
    }
    const mergeAgents = (agents: Record<string, AgentStreamData>): AgentStreamData[] => {
      const list = Object.values(agents)
      const system = list.find(a => a.agent_id === 'system')
      return [system || resumeNotice, ...list.filter(a => a.agent_id !== 'system')]
    }
    let assistantMsg = ensureResumePlaceholder(session, target.turn_id, 0)
    assistantMsg.agents = [resumeNotice]
    let completed = false
    let failures = 0
    let handedOff = false

    while (!controller.signal.aborted) {
      const harness = useHarnessTurn()
      let sawEvents = false
      let fatal: ChatError | null = null
      try {
        await harness.resume(streamId, target, {
          signal: controller.signal,
          onEvent: (event, reduced) => {
            if (controller.signal.aborted) return
            // 服务端已切到新的 turn（旧回合结束后又来了新任务）：不污染当前重建。
            if (event.turn_id && event.turn_id !== target.turn_id) return
            sawEvents = true
            const t = event.type
            if (
              t === 'agent_start' || t === 'agent_step' || t === 'agent_done' ||
              t === 'agent_error' || t === 'context_truncated' ||
              t === 'model_switched' || t === 'text_delta' || t === 'replay_gap'
            ) {
              assistantMsg.agents = mergeAgents(reduced.agents)
              if (reduced.model) assistantMsg.model = reduced.model
              if (t === 'text_delta') assistantMsg.content = reduced.assistantContent
              return
            }
            if (event.conversation_id && !session.conversationId) {
              session.conversationId = event.conversation_id
            }
            if (t === 'queued') {
              session.streamPhase = 'queued'
              session.queuePosition = event.queue_position ?? null
              return
            }
            if (t === 'permission_request') {
              usePermissionStore().handleIncoming({
                id: event.request_id!,
                path: event.path!,
                operation: event.operation!,
                tool_name: event.tool_name!,
                tool_args: event.tool_args as Record<string, unknown>,
                created_at: event.created_at || new Date().toISOString(),
                expires_at: event.expires_at,
              })
              return
            }
            if (t === 'routing') {
              routingStatus.value = event.detail || 'Routing...'
              return
            }
            if (t === 'queue_drain') {
              if (event.queue_size != null) session.queuePosition = event.queue_size
              return
            }
            if (t === 'error') {
              session.streamPhase = 'idle'
              routingStatus.value = ''
              assistantMsg.isError = true
              const errorInfo = classifySSEError(event)
              assistantMsg.errorInfo = errorInfo
              assistantMsg.content = formatErrorMessage(errorInfo)
              if (event.error_type === 'ModelSwitchError') {
                sessionModel.revert(sessionKey)
                setNotice(`切换模型失败：${event.detail || event.error || errorInfo.message}`)
              }
              persistSession(sessionKey)
              if (errorInfo.retryable) startAutoRetry(sessionKey, lastUserText(session))
              completed = true
              return
            }
            if (t === 'done' || t === 'turn_done') {
              if (event.model) sessionModel.setActive(sessionKey, event.model)
              // 重放时，同一 drain 序列里已结束的回合可能已从历史加载：按
              // assistant_msg_id 对齐，丢弃重放占位，避免刷新后出现重复气泡。
              const dup = event.assistant_msg_id
                ? session.messages.find(m => m.id === event.assistant_msg_id && m !== assistantMsg)
                : undefined
              if (dup) {
                session.messages = session.messages.filter(m => m.id !== assistantMsg.id)
                assistantMsg = dup
              } else {
                finalizeTurn(session, event, assistantMsg, reduced.agents)
              }
              if (t === 'done') {
                completed = true
                session.streamPhase = 'idle'
                session.loading = false
                routingStatus.value = ''
                session.conversationId = event.conversation_id || session.conversationId
                session.attachedTurnId = undefined
                persistSession(sessionKey)
                return
              }
              // 同一 drain 序列还有下一轮：新建占位继续接。
              session.streamPhase = 'running'
              routingStatus.value = ''
              assistantMsg = ensureResumePlaceholder(session, target.turn_id, event.seq || 0)
            }
          },
        })
      } catch (err) {
        // 断流：下一步用 /status 判断后台是否仍在执行。
        if (err && typeof err === 'object' && 'statusCode' in err) {
          fatal = err as ChatError
        }
      }
      if (completed || controller.signal.aborted) break

      // 服务端已没有可续接回合（404）：退回历史刷新，而不是报网络错误。
      if (fatal?.statusCode === 404) {
        session.loading = false
        session.streamPhase = 'idle'
        session.queuePosition = null
        session.attachedTurnId = undefined
        removeIfEmptyPlaceholder(session, assistantMsg)
        await loadConversation(sessionKey, false)
        break
      }
      if (fatal && fatal.retryable === false) {
        // 403 等不可重试错误：直接收尾，不进入重试循环。
        session.loading = false
        session.streamPhase = 'idle'
        session.queuePosition = null
        session.attachedTurnId = undefined
        assistantMsg.isError = true
        assistantMsg.errorInfo = fatal
        assistantMsg.content = formatErrorMessage(fatal)
        persistSession(sessionKey)
        break
      }

      const status = await apiGetSessionStatus(streamId).catch(() => null)
      const active = status?.active_turn
      if (active && active.turn_id === target.turn_id) {
        failures = 0
        await sleep(800)
        continue
      }
      if (active) {
        // 旧回合已结束、后台已开始新的 turn：以服务端历史为准并接上新回合。
        session.loading = false
        session.streamPhase = 'idle'
        session.queuePosition = null
        session.attachedTurnId = undefined
        removeIfEmptyPlaceholder(session, assistantMsg)
        await loadConversation(sessionKey, false)
        handedOff = true
        await resumeSession(sessionKey)
        break
      }

      // 后台已无该回合：若事件日志里已有内容，说明回合结束，拉历史补齐权威结果。
      if (sawEvents) {
        session.loading = false
        session.streamPhase = 'idle'
        session.queuePosition = null
        session.attachedTurnId = undefined
        removeIfEmptyPlaceholder(session, assistantMsg)
        await loadConversation(sessionKey, false)
        break
      }

      failures += 1
      if (failures >= 3) {
        session.streamPhase = 'idle'
        session.loading = false
        session.queuePosition = null
        assistantMsg.isError = true
        const errorInfo: ChatError = {
          type: 'network',
          message: '网络连接中断，请检查网络后重试',
          retryable: true,
        }
        assistantMsg.errorInfo = errorInfo
        assistantMsg.content = formatErrorMessage(errorInfo)
        persistSession(sessionKey)
        if (autoRetrySessionId.value === sessionKey) startAutoRetry(sessionKey, lastUserText(session))
        break
      }
      await sleep(1000)
    }

    if (session.abortController === controller) session.abortController = null
    if (handedOff) return
    if (!session.loading) return
    session.loading = false
    session.streamPhase = 'idle'
    session.queuePosition = null
    if (!completed) {
      session.attachedTurnId = undefined
      removeIfEmptyPlaceholder(session, assistantMsg)
    }
  }

  /** 查询会话是否有在途回合；有则自动订阅续流。返回是否接上了活动回合。 */
  async function resumeSession(id: string): Promise<boolean> {
    const session = sessions.value[id]
    if (!session) return false
    const serverId = session.conversationId || id
    let status: SessionStatus | null = null
    try {
      status = await apiGetSessionStatus(serverId)
    } catch {
      return false
    }
    const active = status?.active_turn
    if (!active) return false
    if (session.attachedTurnId === active.turn_id && session.abortController) return true
    await attachTurn(id, session, active)
    return true
  }

  /** 仅解绑本地 SSE（离开页面/切换会话），后台任务继续执行。 */
  function detachStream(): void {
    const id = activeSessionId.value
    const session = id ? sessions.value[id] : undefined
    if (!session) return
    session.abortController?.abort()
    session.abortController = null
    session.loading = false
    session.streamPhase = 'idle'
    session.queuePosition = null
  }

  async function send(text: string, clientMsgId?: string, files?: FileContent[], voice?: VoiceMessageData): Promise<boolean> {
    let sessionId = activeSessionId.value
    if (!sessionId) {
      sessionId = genId()
      getOrCreateSession(sessionId)
      activeSessionId.value = sessionId
      // 新会话继承 composer 的 draft 选择；draft 仍保留给下一个新会话。
      sessionModel.copy(SESSION_MODEL_DRAFT_KEY, sessionId)
    }

    const session = sessions.value[sessionId]
    if (!session) return false
    // 后台切换仍在跑时等待它，避免与新回合的 target_model 切换并发。
    if (session.conversationId) await modelSwitch.wait(sessionId)
    // [队列] 执行中（或本会话还有待处理任务）再次发送 → 不再拒绝，改为「追加排队任务」
    // （对齐 opencode：执行中仍可发消息；顺序 = 新→旧 LIFO，后端当前轮结束后 drain）。
    // pendingPrompts 用 ?. 兜底：历史缓存/测试里手工构造的 session 对象可能没有该字段。
    if (session.loading || session.streamPhase !== 'idle' || (session.pendingPrompts?.length ?? 0) > 0) {
      return enqueueWhileBusy(sessionId, session, text, files, voice)
    }
    const requestedModel = selectedModel.value
    // [S6] 发送前校验模型/目录有效性，避免带无效 model 触发后端 404
    if (!modelOptions.value.some(m => m.value === requestedModel)) {
      setNotice(`当前模型不可用：${requestedModel}，请重新选择`)
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
      startedAt: Date.now(),
    })
    session.messages = [...session.messages, assistantMsg]

    autoRetrySessionId.value = sessionId
    retryMessageText.value = text

    // 发送后立即持久化（SSE 中断也不丢失 user 消息）
    // [S5] 走防抖（非终态写，done/error 的终态 persistSession 会清 timer 兜底覆盖）
    schedulePersist(sessionId)

    const reqData = {
      message: text,
      conversation_id: session.conversationId,
      model: requestedModel,
      // 已有会话：模型随本轮下发，后端在回合开始前切换（opencode per-message model）。
      target_model: session.conversationId ? requestedModel : undefined,
      use_vector_db: useVectorDb.value,
      client_msg_id: messageClientId,
      files: files && files.length ? files : undefined,
      voice: voice || undefined,
      agent_mode: agentMode.value !== 'default' ? agentMode.value : undefined,
    }
    const controller = new AbortController()
    session.abortController = controller
    const signal = controller.signal

    try {
      const harness = useHarnessTurn()
      await harness.run(reqData, {
        signal,
        onSessionId: (sid) => {
          if (!session.conversationId) {
            session.conversationId = sid
          }
        },
        onEvent: (event, turnState) => {
        if (signal.aborted) return

          const reduced = turnState

        if (
          event.type === 'agent_start' ||
          event.type === 'agent_step' ||
          event.type === 'agent_done' ||
          event.type === 'agent_error' ||
          event.type === 'context_truncated' ||
          event.type === 'model_switched' ||
          event.type === 'text_delta'
        ) {
          if (event.type === 'agent_start') routingStatus.value = ''
          agentsMap = reduced.agents
          assistantMsg.agents = Object.values(agentsMap)
          if (reduced.model) assistantMsg.model = reduced.model
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
          // 回合开始前切换失败：服务端仍保留旧模型，UI 也回滚到 active。
          if (event.error_type === 'ModelSwitchError') {
            if (sessionModel.desired(sessionId) === requestedModel) {
              sessionModel.revert(sessionId)
            }
            setNotice(`切换模型失败：${event.detail || event.error || errorInfo.message}`)
          }
          persistSession(sessionId)
          // 自动重试：仅对可重试错误且未超过最大次数
          if (errorInfo.retryable && autoRetrySessionId.value === sessionId) {
            startAutoRetry(sessionId, text)
          }
        } else if (event.type === 'done' || event.type === 'turn_done') {
          if (event.model) sessionModel.setActive(sessionId, event.model)
          // [队列] turn_done = 本回合完成但还有排队任务（同一条 SSE 流继续跑下一个回合）；
          // done = 队列已空，断流收尾。两者共用同一套「回合收尾」逻辑。
          const moreComing = event.type === 'turn_done'
          finalizeTurn(session, event, assistantMsg, agentsMap)
          if (!moreComing) {
            completed = true
            session.streamPhase = 'idle'
            routingStatus.value = ''
            session.conversationId = event.conversation_id || session.conversationId
            session.attachedTurnId = undefined
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
            startedAt: Date.now(),
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
          // 排队任务也记住入队时的模型；后端在它开始执行前切换。
          model: selectedModel.value,
          files: files && files.length ? files : undefined,
          voice: voice || undefined,
          agent_mode: agentMode.value !== 'default' ? agentMode.value : undefined,
        },
        messageClientId,
      )
      userMsg.queuePromptId = row?.id || messageClientId
      session.pendingPrompts = [...(session.pendingPrompts ?? []), { id: userMsg.queuePromptId, message: text, model: selectedModel.value }]
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
        session.attachedTurnId = undefined
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
        s.attachedTurnId = undefined
        if (s.conversationId) {
          try { await interruptSession(s.conversationId) } catch (e) { console.error('Failed to interrupt before undo:', e) }
        }
        if (autoRetrySessionId.value === activeSessionId.value) cancelAutoRetry()
        s.streamPhase = 'idle'
        s.queuePosition = null
        s.loading = false
      }
      // `/sessions/{id}/revert` 语义 = 隐藏该消息**及其之后**，所以传撤销点本身。
      const target = index >= 0 && index < s.messages.length ? s.messages[index] : undefined
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
      if (s) s.attachedTurnId = undefined
      const serverId = s?.conversationId || activeSessionId.value
      if (serverId) { try { await apiDeleteConversation(serverId) } catch (e) { console.error('Failed to delete:', e) } }
      await deleteSessionFromCache(serverId)
      if (serverId !== activeSessionId.value) await deleteSessionFromCache(activeSessionId.value)
      if (serverId) sessionModel.forget(serverId)
      if (activeSessionId.value) sessionModel.forget(activeSessionId.value)
      if (serverId) modelSwitch.forget(serverId)
      if (activeSessionId.value) modelSwitch.forget(activeSessionId.value)
      delete sessions.value[activeSessionId.value]
      activeSessionId.value = undefined
      loadConversations()
    }
  }

  // [模型切换] opencode 语义：选择只写本地 desired，零等待、不 PATCH。
  // 已有会话在下一条消息带 target_model，由后端在回合开始前切换；
  // 切换失败会发 ModelSwitchError，send/attachTurn 再把 desired 回滚到 active。
  async function persistSelectedModel(target?: string): Promise<boolean> {
    const model = target || selectedModel.value
    if (!modelOptions.value.some(o => o.value === model)) {
      setNotice(`模型目录未就绪或模型不存在：${model}`)
      return false
    }
    selectedModel.value = model
    setNotice('')
    const sessionKey = activeSessionId.value
    const session = sessionKey ? sessions.value[sessionKey] : undefined
    if (sessionKey && session?.conversationId && !session.loading && session.streamPhase === 'idle') {
      void modelSwitch.enqueue(sessionKey, model, session.conversationId).then(result => {
        if (result.status !== 'failed') return
        const error = result.error as { message?: unknown } | undefined
        const detail = error?.message != null ? String(error.message) : String(result.error || '未知错误')
        setNotice(`切换模型失败：${detail}`)
      })
    }
    return true
  }

  return {
    sessions, activeSessionId, conversations, routingStatus, selectedModel, useVectorDb,
    models, modelOptions, defaultModelId, smallModelId, smallModelInfo, loadModels, persistSelectedModel,
    imageCaptionModelId, imageCaptionModelInfo, voiceModelSize,
    agentMode,
    messages, conversationId, conversationTitle, loading, streamPhase, queuePosition,
    pendingPrompts, removeQueuedPrompt,
    retryCountdown, notice, setNotice,
    send, cancel, clear, undoMessage, restoreSnapshot, fetchMessageDiff, deleteMessage, deleteConversation,
    resumeSession, detachStream,
    loadConversations, loadConversation, newChat, renameConversation,
    forkConversation, compactConversation, fetchSessionStatus,
    retryLastMessage, manualRetry, cancelAutoRetry,
  }
})
