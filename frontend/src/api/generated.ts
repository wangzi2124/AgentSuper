import type { GeneratedFileList } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/generated'

export async function listGenerated(q?: string): Promise<GeneratedFileList> {
  const params = q ? `?q=${encodeURIComponent(q)}` : ''
  const res = await fetchWithTimeout(BASE + '/' + params)
  if (!res.ok) throw new Error(`Failed to list generated files: ${res.statusText}`)
  return res.json()
}

export async function deleteGenerated(filename: string): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(filename), { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete failed: ${err || res.statusText}`)
  }
}
