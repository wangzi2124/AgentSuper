import type { Skill } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/skills'

export async function listSkills(): Promise<Skill[]> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list skills: ${res.statusText}`)
  return res.json()
}

export async function toggleSkill(name: string, enabled: boolean): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Toggle skill failed: ${res.statusText}`)
}
