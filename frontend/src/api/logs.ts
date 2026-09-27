import { apiRequest } from './errors'

// 全链路日志 API 客户端（后端 /api/logs）
const BASE = '/api/logs'

// 链路阶段：与后端 chainlog.STAGES 对齐（用于筛选下拉与时间线分组着色）
export const STAGES = [
  'http', 'session', 'agent', 'tool', 'llm', 'permission', 'memory', 'vector', 'persist', 'system',
] as const

export const STAGE_LABELS: Record<string, string> = {
  http: 'HTTP',
  session: '会话',
  agent: '智能体',
  tool: '工具',
  llm: '模型调用',
  permission: '权限审批',
  memory: '记忆',
  vector: '知识库',
  persist: '落库',
  system: '系统',
}

export const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const

export const LEVEL_LABELS: Record<string, string> = {
  DEBUG: '调试', INFO: '信息', WARNING: '警告', ERROR: '错误', CRITICAL: '严重',
}

// 一条链路日志节点（后端 chain_logs 行）
export interface ChainLogEntry {
  id: string
  trace_id: string
  parent_id: string
  seq: number
  ts: number
  time: number
  level: string
  stage: string
  component: string
  event: string
  agent_id: string
  session_id: string
  user_id: string
  path: string
  message: string
  data: Record<string, any>
  duration_ms: number | null
}

// 一次请求的聚合摘要（后端 list_traces 行）
export interface ChainTraceSummary {
  trace_id: string
  start_ts: number
  end_ts: number
  duration_ms: number
  nodes: number
  errors: number
  warnings: number
  session_id: string
  user_id: string
  path: string
  title: string
}

export interface Paged<T> {
  entries?: T[]
  traces?: T[]
  total: number
  offset: number
  limit: number
}

export interface TraceDetail {
  trace_id: string
  entries: ChainLogEntry[]
  total: number
}

export interface ChainLogFilters {
  session_id?: string
  level?: string
  stage?: string
  component?: string
  agent_id?: string
  keyword?: string
  has_error?: boolean
  since?: number
  until?: number
}

export interface ChainLogStats {
  total: number
  traces: number
  by_level: Record<string, number>
  by_stage: Record<string, number>
  by_component: Record<string, number>
  by_agent: Record<string, number>
  avg_duration_ms: number
  slow_traces: Array<{ trace_id: string; duration_ms: number; nodes: number }>
  top_errors: Array<{ event: string; component: string; n: number }>
  counters: {
    queued: number
    queue_size: number
    dropped: number
    errors: number
    written: number
  }
}

export interface FilterOptions {
  components: string[]
  agents: string[]
  sessions: string[]
  stages: string[]
  levels: string[]
}

function query(filters: ChainLogFilters, extra: Record<string, any> = {}): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries({ ...filters, ...extra })) {
    if (v === undefined || v === null || v === '' || v === false) continue
    p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

// 链路列表（一次请求一行）
export function listTraces(
  filters: ChainLogFilters,
  offset = 0,
  limit = 30,
): Promise<Paged<ChainTraceSummary>> {
  return apiRequest(`${BASE}${query(filters, { view: 'traces', offset, limit })}`)
}

// 原始日志条目列表
export function listEntries(
  filters: ChainLogFilters,
  offset = 0,
  limit = 50,
  order: 'asc' | 'desc' = 'desc',
): Promise<Paged<ChainLogEntry>> {
  return apiRequest(`${BASE}${query(filters, { view: 'entries', offset, limit, order })}`)
}

// 单条链路时间线
export function getTrace(traceId: string, limit = 2000): Promise<TraceDetail> {
  return apiRequest(`${BASE}/traces/${encodeURIComponent(traceId)}?limit=${limit}`)
}

// 聚合统计
export function getStats(filters: ChainLogFilters = {}): Promise<ChainLogStats> {
  return apiRequest(`${BASE}/stats${query(filters)}`)
}

// 筛选下拉取值
export function getFilterOptions(): Promise<FilterOptions> {
  return apiRequest(`${BASE}/filters`)
}

// 按 TTL / 行数上限裁剪（管理员）
export function cleanupLogs(): Promise<{ expired: number; overflow: number }> {
  return apiRequest(`${BASE}/cleanup`, { method: 'POST' })
}

// 清理日志：按 trace_id / before 水位 / 全量（管理员）
export function clearLogs(payload: { trace_id?: string; before?: number; all?: boolean } = {}): Promise<{ deleted: number }> {
  return apiRequest(`${BASE}/clear`, { method: 'POST', body: JSON.stringify(payload) })
}
