import type { Plugin } from '../types'
import { fetchWithTimeout } from './fetch'

// 插件 API 基础路径
const BASE = '/api/plugins'

// 获取所有插件列表
export async function listPlugins(): Promise<Plugin[]> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list plugins: ${res.statusText}`)
  return res.json()
}

// 切换插件启用/禁用状态
export async function togglePlugin(name: string, enabled: boolean): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Toggle plugin failed: ${res.statusText}`)
}
