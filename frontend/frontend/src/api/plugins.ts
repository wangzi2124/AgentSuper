import type { Plugin } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/plugins'

export async function listPlugins(): Promise<Plugin[]> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list plugins: ${res.statusText}`)
  return res.json()
}

export async function togglePlugin(name: string, enabled: boolean): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Toggle plugin failed: ${res.statusText}`)
}
