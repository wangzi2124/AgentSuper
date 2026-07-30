const STORAGE_KEY = 'agent_super_user_id'

export function getUserId(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || 'anonymous'
  } catch {
    return 'anonymous'
  }
}

export function setUserId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch { /* noop */ }
}
