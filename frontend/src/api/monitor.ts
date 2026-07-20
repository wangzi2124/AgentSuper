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
