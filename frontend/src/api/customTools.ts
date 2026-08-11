// 自定义工具 API [PATCH6]
import type { CustomToolItem, ToolCatalogItem } from '../types/customTools'
import { fetchWithTimeout } from './fetch'

// 自定义工具 API 基础路径
const BASE = '/api/custom-tools'

// 获取自定义工具列表（脚本型 + 固定型）
export async function listCustomTools(): Promise<CustomToolItem[]> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list custom tools: ${res.statusText}`)
  return res.json()
}

// 获取工具目录（当前所有可用工具，供固定已有工具）
export async function getToolCatalog(): Promise<ToolCatalogItem[]> {
  const res = await fetchWithTimeout(BASE + '/catalog')
  if (!res.ok) throw new Error(`Failed to load tool catalog: ${res.statusText}`)
  return res.json()
}

// 创建脚本型自定义工具
export async function createScriptTool(payload: {
  name: string
  description?: string
  script: string
  enabled?: boolean
}): Promise<CustomToolItem> {
  const res = await fetchWithTimeout(BASE + '/script', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.detail || `Create script tool failed: ${res.statusText}`)
  }
  return res.json()
}

// 固定一个已有工具（按需挂载时始终保留其 schema）
export async function pinTool(payload: { tool_name: string; description?: string }): Promise<CustomToolItem> {
  const res = await fetchWithTimeout(BASE + '/pin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.detail || `Pin tool failed: ${res.statusText}`)
  }
  return res.json()
}

// 切换启用/禁用
export async function toggleCustomTool(name: string, enabled: boolean): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Toggle custom tool failed: ${res.statusText}`)
}

// 删除自定义工具
export async function deleteCustomTool(name: string): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name), {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Delete custom tool failed: ${res.statusText}`)
}
