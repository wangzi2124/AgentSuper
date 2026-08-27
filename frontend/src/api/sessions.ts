import { fetchWithTimeout } from './fetch'

// ===== 类型（对齐后端 app/session/models.py）=====

export interface SessionModelRef {
  id: string
  providerID: string
  variant?: string | null
}

export interface SessionInfo {
  id: string
  slug: string
  user_id: string
  project_id: string
  workspace_id?: string | null
  parent_id?: string | null
  directory: string
  path: string
  title: string
  agent?: string | null
  model?: SessionModelRef | null
  kind: string
  status: string
  cost: number
  tokens_input: number
  tokens_output: number
  tokens_cache_read: number
  tokens_cache_write: number
  time_created: number
  time_updated: number
  time_compacted?: number | null
  time_archived?: number | null
}

export interface SessionPart {
  id: string
  session_id: string
  message_id: string
  type: string
  data: Record<string, unknown>
  time_created: number
}

export interface SessionMessage {
  id: string
  session_id: string
  type: string
  role?: string | null
  content: string
  data: Record<string, unknown>
  seq: number
  time_created: number
  parts?: SessionPart[]
}

export interface SessionStatus {
  session_id: string
  status: string
}

export interface RevertResult {
  deleted: number
  messages: SessionMessage[]
}

interface SessionCreateBody {
  project_id?: string
  parent_id?: string
  agent?: string
  model?: SessionModelRef
  kind?: string
  title?: string
  directory?: string
}

interface SessionUpdateBody {
  title?: string
  archived?: number
  agent?: string
  model?: SessionModelRef
}

const BASE = '/api/sessions'

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

async function request<T>(path: string, init?: RequestInit, timeout = 10000): Promise<T> {
  const res = await fetchWithTimeout(path, init, timeout)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`${res.status} ${err || res.statusText}`)
  }
  return res.json()
}

// ===== 会话 CRUD =====

export async function createSession(body: SessionCreateBody): Promise<SessionInfo> {
  return request<SessionInfo>(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, 10000)
}

export async function listSessions(opts?: {
  project_id?: string
  workspace_id?: string
  roots?: boolean
  search?: string
  archived?: boolean
  kind?: string
  limit?: number
}): Promise<SessionInfo[]> {
  return request<SessionInfo[]>(`${BASE}${qs({
    project_id: opts?.project_id,
    workspace_id: opts?.workspace_id,
    roots: opts?.roots,
    search: opts?.search,
    archived: opts?.archived,
    kind: opts?.kind,
    limit: opts?.limit,
  })}`, { method: 'GET' }, 15000)
}

export async function getSession(sessionId: string): Promise<SessionInfo> {
  return request<SessionInfo>(`${BASE}/${encodeURIComponent(sessionId)}`, { method: 'GET' }, 10000)
}

export async function updateSession(sessionId: string, body: SessionUpdateBody): Promise<SessionInfo> {
  return request<SessionInfo>(`${BASE}/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, 10000)
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }, 10000)
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete session error: ${err || res.statusText}`)
  }
}

// 删除会话中的单条消息（及其 parts）；前端仍以墓碑防止缓存复活
export async function deleteSessionMessage(sessionId: string, messageId: string): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
    { method: 'DELETE' },
    10000,
  )
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete session message error: ${err || res.statusText}`)
  }
}

// ===== 会话操作 =====

export async function forkSession(sessionId: string, messageId?: string): Promise<SessionInfo> {
  return request<SessionInfo>(`${BASE}/${encodeURIComponent(sessionId)}/fork${qs({ message_id: messageId })}`, {
    method: 'POST',
  }, 15000)
}

export async function getSessionMessages(
  sessionId: string,
  opts?: { after_seq?: number; limit?: number },
): Promise<SessionMessage[]> {
  return request<SessionMessage[]>(`${BASE}/${encodeURIComponent(sessionId)}/messages${qs({
    after_seq: opts?.after_seq,
    limit: opts?.limit,
  })}`, { method: 'GET' }, 15000)
}

export async function getSessionContext(sessionId: string): Promise<{ session_id: string; epoch?: unknown; history: unknown[] }> {
  return request<{ session_id: string; epoch?: unknown; history: unknown[] }>(
    `${BASE}/${encodeURIComponent(sessionId)}/context`, { method: 'GET' }, 10000,
  )
}

export async function compactSession(sessionId: string, checkpoint?: string): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/${encodeURIComponent(sessionId)}/compact${qs({ checkpoint })}`,
    { method: 'POST' },
    30000,
  )
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Compact session error: ${err || res.statusText}`)
  }
}

export async function revertSession(
  sessionId: string,
  messageId: string,
): Promise<RevertResult> {
  const res = await fetchWithTimeout(
    `${BASE}/${encodeURIComponent(sessionId)}/revert`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_id: messageId }),
    },
    15000,
  )
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Revert session error: ${err || res.statusText}`)
  }
  return res.json()
}

// 中断正在运行的会话：真正停止后台 Agent 任务（配合前端本地 abort 双保险）
export async function interruptSession(sessionId: string): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/${encodeURIComponent(sessionId)}/interrupt`,
    { method: 'POST' },
    5000,
  )
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Interrupt session error: ${err || res.statusText}`)
  }
}

export async function getSessionChildren(sessionId: string): Promise<SessionInfo[]> {
  return request<SessionInfo[]>(`${BASE}/${encodeURIComponent(sessionId)}/children`, { method: 'GET' }, 10000)
}

export async function getSessionStatus(sessionId: string): Promise<SessionStatus> {
  return request<SessionStatus>(`${BASE}/${encodeURIComponent(sessionId)}/status`, { method: 'GET' }, 10000)
}

// ===== 会话列表/详情便捷封装（对齐旧 /api/chat/conversations* 形状，走新 REST）=====

export interface ConversationMeta {
  id: string
  title: string
  directory?: string
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends ConversationMeta {
  messages: Array<{ id: string; role: string; content: string; sources?: any[]; steps?: any[]; parts?: any[]; agents?: any[]; files?: any[]; seq?: number }>
}

function _msgTypeToRole(msgType: string): string {
  return {
    user: 'user', assistant: 'assistant', tool: 'tool',
    compaction: 'system', epoch: 'system', system: 'system',
  }[msgType] || 'system'
}

function _fmtTime(ms: number): string {
  return ms ? new Date(ms).toISOString() : ''
}

// 列表：kind 对应旧 conv_type（chat / multi-agent）
export async function listConversations(convType?: string): Promise<ConversationMeta[]> {
  const sessions = await listSessions({ kind: convType, archived: false, limit: 1000 })
  return sessions.map(s => ({
    id: s.id,
    title: s.title || '新对话',
    directory: s.directory || '',
    created_at: _fmtTime(s.time_created),
    updated_at: _fmtTime(s.time_updated),
  }))
}

// 详情：会话信息 + 全部消息（含 parts）
export async function getConversation(conversationId: string, convType?: string): Promise<ConversationDetail> {
  const [session, messages] = await Promise.all([
    getSession(conversationId),
    getSessionMessages(conversationId),
  ])
  if (convType && session.kind !== convType) {
    throw new Error('404 Conversation not found')
  }
  return {
    id: session.id,
    title: session.title || '新对话',
    directory: session.directory || '',
    created_at: _fmtTime(session.time_created),
    updated_at: _fmtTime(session.time_updated),
    messages: messages.map(m => {
      const msg: ConversationDetail['messages'][number] = {
        id: m.id,
        role: m.role || _msgTypeToRole(m.type),
        content: (m.data?.content as string) ?? m.content ?? '',
        seq: m.seq,
      }
      if (m.data?.sources) msg.sources = m.data.sources as any[]
      if (m.data?.steps) msg.steps = m.data.steps as any[]
      if (m.data?.agents) msg.agents = m.data.agents as any[]
      if (m.data?.files) msg.files = m.data.files as any[]
      if (m.parts?.length) msg.parts = m.parts as any[]
      return msg
    }),
  }
}

// 重命名标题
export async function renameConversation(conversationId: string, title: string, _convType?: string): Promise<void> {
  await updateSession(conversationId, { title })
}

// 删除会话
export async function deleteConversation(conversationId: string, _convType?: string): Promise<void> {
  await deleteSession(conversationId)
}
