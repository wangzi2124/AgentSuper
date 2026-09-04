import type { MonitorStats } from '../types'
import { fetchWithTimeout } from './fetch'

// 监控 API 基础路径
const BASE = '/api/monitor'

// 获取系统监控统计数据
export async function fetchStats(): Promise<MonitorStats> {
  const res = await fetchWithTimeout(BASE + '/stats')
  if (!res.ok) throw new Error(`Failed to fetch monitor stats: ${res.statusText}`)
  return res.json()
}

export interface UserUsage {
  user_id?: string
  sessions: number
  requests: number
  tokens_input: number
  tokens_output: number
}

// 个人 LLM 用量：按当前登录用户跨会话累计（后端 /api/monitor/usage）
export async function fetchUsage(): Promise<UserUsage> {
  const res = await fetchWithTimeout(BASE + '/usage')
  if (!res.ok) throw new Error(`Failed to fetch usage: ${res.statusText}`)
  return res.json()
}
