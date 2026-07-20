import type { Skill } from '../types'
import { fetchWithTimeout } from './fetch'

// 技能 API 基础路径
const BASE = '/api/skills'

// 获取所有技能列表
export async function listSkills(): Promise<Skill[]> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list skills: ${res.statusText}`)
  return res.json()
}

// 切换技能启用/禁用状态
export async function toggleSkill(name: string, enabled: boolean): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Toggle skill failed: ${res.statusText}`)
}
