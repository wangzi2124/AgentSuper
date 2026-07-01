import type { MonitorStats } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/monitor'

export async function fetchStats(): Promise<MonitorStats> {
  const res = await fetchWithTimeout(BASE + '/stats')
  if (!res.ok) throw new Error(`Failed to fetch monitor stats: ${res.statusText}`)
  return res.json()
}
