import type { Skill, SkillDetail, SkillSources } from '../types'
import { fetchWithTimeout } from './fetch'

// 技能 API（受管库 data/skills + 用户追加的外部技能源）
const BASE = '/api/skills'

async function unwrapError(res: Response, fallback: string): Promise<Error> {
  const err = await res.json().catch(() => null)
  return new Error(err?.detail || err?.message || `${fallback}: ${res.statusText}`)
}

// 获取所有技能列表（受管库 + 外部源）
export async function listSkills(): Promise<Skill[]> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw await unwrapError(res, 'Failed to list skills')
  return res.json()
}

// 获取单个技能详情（含完整正文，供编辑表单回填）
export async function getSkill(name: string): Promise<SkillDetail> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name))
  if (!res.ok) throw await unwrapError(res, 'Failed to get skill')
  return res.json()
}

// 新建技能（写入受管库 data/skills/<name>/SKILL.md）
export async function createSkill(payload: {
  name: string
  description?: string
  content?: string
  disable_model_invocation?: boolean
  enabled?: boolean
}): Promise<Skill> {
  const res = await fetchWithTimeout(BASE + '/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await unwrapError(res, 'Create skill failed')
  return res.json()
}

// 编辑受管库中的技能
export async function updateSkill(
  name: string,
  payload: {
    description?: string
    content?: string
    disable_model_invocation?: boolean
    enabled?: boolean
  },
): Promise<Skill> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await unwrapError(res, 'Update skill failed')
  return res.json()
}

// 删除受管库中的技能（外部源的技能后端会拒绝）
export async function deleteSkill(name: string): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name), { method: 'DELETE' })
  if (!res.ok) throw await unwrapError(res, 'Delete skill failed')
}

// 切换技能启用/禁用状态（写回 SKILL.md frontmatter）
export async function toggleSkill(name: string, enabled: boolean): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw await unwrapError(res, 'Toggle skill failed')
}

// 获取技能源（受管库 + 外部源列表）
export async function getSkillsSources(): Promise<SkillSources> {
  const res = await fetchWithTimeout(BASE + '/directory')
  if (!res.ok) throw await unwrapError(res, 'Failed to get skills sources')
  return res.json()
}

/** 追加一个外部技能源（不替换受管库，自建技能不会因此消失） */
export async function addSkillsDirectory(
  directory: string,
): Promise<SkillSources & { skills: Skill[] }> {
  const res = await fetchWithTimeout(BASE + '/directory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ directory }),
  })
  if (!res.ok) throw await unwrapError(res, 'Add skills directory failed')
  return res.json()
}

/** 移除一个外部技能源 */
export async function removeSkillsDirectory(
  directory: string,
): Promise<SkillSources & { skills: Skill[] }> {
  const res = await fetchWithTimeout(BASE + '/directory/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ directory }),
  })
  if (!res.ok) throw await unwrapError(res, 'Remove skills directory failed')
  return res.json()
}
