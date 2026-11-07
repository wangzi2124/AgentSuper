import type {
  FormInfo,
  InboxInfo,
  MessageV2,
  ModelRef,
  PermissionRequest,
  PromptInput,
  SessionInfoV2,
} from '../contracts/v2'
import { apiRequest } from './errors'

const BASE = '/api/v2'

function unwrap<T>(payload: unknown): T {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return (payload as { data: T }).data
  }
  return payload as T
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const payload = await apiRequest<unknown>(BASE + path, options)
  return unwrap<T>(payload)
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

export interface SessionCreateV2 {
  id?: string
  parent_id?: string | null
  title?: string
  agent?: string | null
  model?: ModelRef | null
  directory?: string | null
}

export interface SessionPatchV2 {
  title?: string
  metadata?: Record<string, unknown>
  archived?: boolean
}

export interface SessionListParams {
  limit?: number
  order?: 'asc' | 'desc'
  search?: string
  archived?: boolean
  parentID?: string
}

export interface MessageListResponse {
  data: MessageV2[]
  cursor: { previous: string | null; next: string | null }
}

export async function createSession(body: SessionCreateV2): Promise<SessionInfoV2> {
  return request('/session', json('POST', body))
}

export async function listSessions(params: SessionListParams = {}): Promise<SessionInfoV2[]> {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.order) query.set('order', params.order)
  if (params.search) query.set('search', params.search)
  if (params.archived != null) query.set('archived', String(params.archived))
  if (params.parentID) query.set('parentID', params.parentID)
  const suffix = query.toString() ? `?${query}` : ''
  return request(`/session${suffix}`)
}

export async function getSession(sessionId: string): Promise<SessionInfoV2> {
  return request(`/session/${encodeURIComponent(sessionId)}`)
}

export async function updateSession(
  sessionId: string,
  body: SessionPatchV2,
): Promise<SessionInfoV2> {
  return request(`/session/${encodeURIComponent(sessionId)}`, json('PATCH', body))
}

export async function deleteSession(sessionId: string): Promise<void> {
  await request(`/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export async function prompt(
  sessionId: string,
  body: PromptInput,
): Promise<InboxInfo> {
  return request(`/session/${encodeURIComponent(sessionId)}/prompt`, json('POST', body))
}

export async function listInbox(sessionId: string, includeResolved = false): Promise<InboxInfo[]> {
  const suffix = includeResolved ? '?include_resolved=true' : ''
  return request(`/session/${encodeURIComponent(sessionId)}/inbox${suffix}`)
}

export async function updateInboxDelivery(
  sessionId: string,
  inboxId: string,
  delivery: 'steer' | 'queue',
): Promise<InboxInfo> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/inbox/${encodeURIComponent(inboxId)}`,
    json('PATCH', { delivery }),
  )
}

export async function cancelInbox(sessionId: string, inboxId: string): Promise<void> {
  await request(
    `/session/${encodeURIComponent(sessionId)}/inbox/${encodeURIComponent(inboxId)}`,
    { method: 'DELETE' },
  )
}

export async function interruptSession(sessionId: string, resume = false): Promise<boolean> {
  const suffix = resume ? '?resume=true' : ''
  const data = await request<{ interrupted: boolean }>(
    `/session/${encodeURIComponent(sessionId)}/interrupt${suffix}`,
    json('POST'),
  )
  return !!data.interrupted
}

export async function listMessages(
  sessionId: string,
  params: { limit?: number; order?: 'asc' | 'desc'; type?: string } = {},
): Promise<MessageListResponse> {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.order) query.set('order', params.order)
  if (params.type) query.set('type', params.type)
  const suffix = query.toString() ? `?${query}` : ''
  return apiRequest<MessageListResponse>(
    `${BASE}/session/${encodeURIComponent(sessionId)}/message${suffix}`,
  )
}

export async function compactSession(sessionId: string): Promise<InboxInfo> {
  return request(`/session/${encodeURIComponent(sessionId)}/compact`, json('POST'))
}

export async function stageRevert(
  sessionId: string,
  messageId: string,
  files = false,
): Promise<{ message_id: string; files: boolean }> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/revert/stage`,
    json('POST', { message_id: messageId, files }),
  )
}

export async function commitRevert(sessionId: string): Promise<unknown> {
  return request(`/session/${encodeURIComponent(sessionId)}/revert/commit`, json('POST'))
}

export async function clearRevert(sessionId: string): Promise<void> {
  await request(`/session/${encodeURIComponent(sessionId)}/revert`, { method: 'DELETE' })
}

export async function forkSession(
  sessionId: string,
  before?: string,
): Promise<SessionInfoV2> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/fork`,
    json('POST', { before: before ?? null }),
  )
}

export interface TurnDiffFile {
  file: string
  diff: string
}

export interface TurnDiff {
  files: TurnDiffFile[]
  truncated: boolean
  reason: string
  steps: number
}

export async function fetchTurnDiff(
  sessionId: string,
  messageId: string,
  file?: string,
): Promise<TurnDiff> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/diff`,
    json('POST', { message_id: messageId, file: file ?? null }),
  )
}

export async function restoreTurnSnapshot(
  sessionId: string,
  messageId: string,
): Promise<{ restored: boolean; restored_files: string[]; missing: string[] }> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/restore`,
    json('POST', { message_id: messageId }),
  )
}

export async function switchSessionModel(
  sessionId: string,
  model: ModelRef,
): Promise<SessionInfoV2> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/model`,
    json('POST', { model }),
  )
}

export async function switchSessionAgent(
  sessionId: string,
  agent: string,
): Promise<SessionInfoV2> {
  return request(
    `/session/${encodeURIComponent(sessionId)}/agent`,
    json('POST', { agent }),
  )
}

export async function listPermissions(sessionId: string): Promise<PermissionRequest[]> {
  const payload = await apiRequest<unknown>(
    `${BASE}/session/${encodeURIComponent(sessionId)}/permission`,
  )
  return unwrap<PermissionRequest[]>(payload)
}

export async function replyPermission(
  sessionId: string,
  requestId: string,
  decision: 'once' | 'always' | 'reject',
): Promise<void> {
  await request(
    `/session/${encodeURIComponent(sessionId)}/permission/${encodeURIComponent(requestId)}/reply`,
    json('POST', { decision }),
  )
}

export async function listForms(sessionId: string): Promise<FormInfo[]> {
  const payload = await apiRequest<unknown>(
    `${BASE}/session/${encodeURIComponent(sessionId)}/form`,
  )
  return unwrap<FormInfo[]>(payload)
}

export async function replyForm(
  sessionId: string,
  formId: string,
  response: Record<string, unknown>,
): Promise<void> {
  await request(
    `/session/${encodeURIComponent(sessionId)}/form/${encodeURIComponent(formId)}/reply`,
    json('POST', { response }),
  )
}
