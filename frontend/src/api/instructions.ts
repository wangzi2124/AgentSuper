import { fetchWithTimeout } from './fetch'

export interface InstructionSourceMeta {
  id: string
  kind: string
  location: string
  scope: string
  mtime: number
  hash: string
  loaded: boolean
  skipped_reason: string
  chars: number
  tokens: number
}

export interface InstructionListResult {
  enabled: boolean
  workspace: string
  scope: 'project' | 'global'
  entries: string[]
  auto_discover: boolean
  sources: InstructionSourceMeta[]
  loaded_count: number
  total_chars: number
  total_tokens: number
}

export interface BrowseEntry {
  name: string
  path: string
}

export interface BrowseFilesResult {
  path: string
  parent: string
  name: string
  dirs: BrowseEntry[]
  files: BrowseEntry[]
}

function query(sessionId?: string): string {
  return sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
}

async function readError(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  return body?.detail || `${res.status} ${res.statusText}`
}

export async function listInstructions(sessionId?: string): Promise<InstructionListResult> {
  const res = await fetchWithTimeout(`/api/instructions${query(sessionId)}`)
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as InstructionListResult
}

export async function addInstructionEntry(entry: string, sessionId: string): Promise<string[]> {
  const res = await fetchWithTimeout(`/api/instructions/entries${query(sessionId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entry }),
  })
  if (!res.ok) throw new Error(await readError(res))
  const body = (await res.json()) as { entries?: string[] }
  return body.entries ?? []
}

export async function removeInstructionEntry(entry: string, sessionId: string): Promise<string[]> {
  const res = await fetchWithTimeout(`/api/instructions/entries${query(sessionId)}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entry }),
  })
  if (!res.ok) throw new Error(await readError(res))
  const body = (await res.json()) as { entries?: string[] }
  return body.entries ?? []
}

export async function setInstructionAutoDiscover(
  autoDiscover: boolean,
  sessionId: string,
): Promise<boolean> {
  const res = await fetchWithTimeout(`/api/instructions/settings${query(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_discover: autoDiscover }),
  })
  if (!res.ok) throw new Error(await readError(res))
  const body = (await res.json()) as { auto_discover?: boolean }
  return body.auto_discover ?? autoDiscover
}

export async function browseFiles(path: string): Promise<BrowseFilesResult> {
  const res = await fetchWithTimeout(
    `/api/permission/browse?files=true&path=${encodeURIComponent(path)}`,
  )
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as BrowseFilesResult
}
