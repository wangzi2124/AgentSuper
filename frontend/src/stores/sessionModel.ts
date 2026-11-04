import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * Per-session model preference (opencode semantics).
 *
 * `desired` is what the user picked for a session; it is persisted locally and
 * only becomes the server session model when the next turn starts.
 * `active` is the model the server actually used for the last completed turn.
 *
 * The draft key is used before a new conversation exists (composer with no
 * active session). It is copied onto the real session key on first send.
 */
export const SESSION_MODEL_DRAFT_KEY = '__draft__'
const STORAGE_KEY = 'agentsuper:session-model:v1'
const STORAGE_VERSION = 1

interface PersistedSelection {
  version?: number
  desired?: Record<string, string>
}

function loadDesired(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as PersistedSelection
    if (!parsed || parsed.version !== STORAGE_VERSION || !parsed.desired) return {}
    const out: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed.desired)) {
      if (typeof key === 'string' && typeof value === 'string' && key && value) {
        out[key] = value
      }
    }
    return out
  } catch {
    return {}
  }
}

export const useSessionModelStore = defineStore('sessionModel', () => {
  const desiredBySession = ref<Record<string, string>>(loadDesired())
  const activeBySession = ref<Record<string, string>>({})

  function _persist(): void {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ version: STORAGE_VERSION, desired: desiredBySession.value }),
      )
    } catch {
      // Storage unavailable (private mode/quota): keep in-memory preference.
    }
  }

  function desired(sessionKey: string = SESSION_MODEL_DRAFT_KEY): string | undefined {
    return desiredBySession.value[sessionKey]
  }

  function active(sessionKey: string = SESSION_MODEL_DRAFT_KEY): string | undefined {
    return activeBySession.value[sessionKey]
  }

  function select(sessionKey: string, model: string): void {
    if (!sessionKey || !model) return
    desiredBySession.value = { ...desiredBySession.value, [sessionKey]: model }
    _persist()
  }

  function setActive(sessionKey: string, model: string): void {
    if (!sessionKey || !model) return
    activeBySession.value = { ...activeBySession.value, [sessionKey]: model }
  }

  /** Drop the local preference; the next read falls back to the active model. */
  function revert(sessionKey: string): void {
    if (!(sessionKey in desiredBySession.value)) return
    const next = { ...desiredBySession.value }
    delete next[sessionKey]
    desiredBySession.value = next
    _persist()
  }

  /** Copy preference when the client session key becomes the server id. */
  function copy(fromKey: string, toKey: string): void {
    if (!fromKey || !toKey || fromKey === toKey) return
    let changed = false
    const desiredModel = desiredBySession.value[fromKey]
    if (desiredModel && !desiredBySession.value[toKey]) {
      desiredBySession.value = { ...desiredBySession.value, [toKey]: desiredModel }
      changed = true
    }
    const activeModel = activeBySession.value[fromKey]
    if (activeModel && !activeBySession.value[toKey]) {
      activeBySession.value = { ...activeBySession.value, [toKey]: activeModel }
    }
    if (changed) _persist()
  }

  function forget(sessionKey: string): void {
    let changed = false
    if (sessionKey in desiredBySession.value) {
      const next = { ...desiredBySession.value }
      delete next[sessionKey]
      desiredBySession.value = next
      changed = true
    }
    if (sessionKey in activeBySession.value) {
      const next = { ...activeBySession.value }
      delete next[sessionKey]
      activeBySession.value = next
    }
    if (changed) _persist()
  }

  return {
    desiredBySession,
    activeBySession,
    desired,
    active,
    select,
    setActive,
    revert,
    copy,
    forget,
  }
})
