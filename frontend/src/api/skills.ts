import type { Skill } from '../types'
import { fetchWithTimeout } from './fetch'

// 技能 API 基础路径（技能目录由前端在自定义工具页选择）
const BASE = '/api/skills'

// 获取所有技能列表（扫描当前技能目录）
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
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.detail || `Toggle skill failed: ${res.statusText}`)
  }
}

// 获取当前技能目录
export async function getSkillsDirectory(): Promise<{ directory: string }> {
  const res = await fetchWithTimeout(BASE + '/directory')
  if (!res.ok) throw new Error(`Failed to get skills directory: ${res.statusText}`)
  return res.json()
}

// 设置技能目录（前端选择文件夹后热加载）
export async function setSkillsDirectory(directory: string): Promise<{ directory: string; skills: Skill[] }> {
  const res = await fetchWithTimeout(BASE + '/directory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ directory }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.detail || `Set skills directory failed: ${res.statusText}`)
  }
  return res.json()
}