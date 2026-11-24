import type { ConversationMeta } from '../api/sessions'

const RECENTS_KEY = 'agentsuper:recent-workspace-paths'
const RECENTS_LIMIT = 8

export function normPath(path?: string | null): string {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

export function baseName(path: string): string {
  const parts = String(path || '').split(/[\\/]+/).filter(Boolean)
  return parts[parts.length - 1] || path
}

export function isUnder(candidate: string, root: string): boolean {
  const c = normPath(candidate)
  const r = normPath(root)
  return !!c && !!r && (c === r || c.startsWith(r + '/'))
}

export function workspaceRootFor(directory: string | null | undefined, workspaces: string[]): string | null {
  if (!directory) return null
  let best: string | null = null
  for (const w of workspaces) {
    if (isUnder(directory, w) && (!best || normPath(w).length > normPath(best).length)) best = w
  }
  return best
}

export function queryTokens(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean)
}

export function matchesTokens(text: string | null | undefined, tokens: string[]): boolean {
  const haystack = String(text || '').toLowerCase()
  return tokens.every(token => haystack.includes(token))
}

export function matchesConversation(
  c: ConversationMeta,
  tokens: string[],
  root?: string | null,
): boolean {
  if (!tokens.length) return true
  return matchesTokens(
    [c.title, c.directory || '', baseName(c.directory || ''), root || '', baseName(root || '')].join(' '),
    tokens,
  )
}

export function loadRecentPaths(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((p): p is string => typeof p === 'string' && !!p) : []
  } catch {
    return []
  }
}

export function rememberRecentPath(path: string): string[] {
  const clean = String(path || '').trim()
  if (!clean) return loadRecentPaths()
  const next = [clean, ...loadRecentPaths().filter(p => normPath(p) !== normPath(clean))].slice(0, RECENTS_LIMIT)
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next))
  } catch {
    return next
  }
  return next
}
