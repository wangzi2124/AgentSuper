import { defineStore } from 'pinia'
import { ref } from 'vue'
import { updateSession } from '../api/sessions'
import { SESSION_MODEL_DRAFT_KEY, useSessionModelStore } from './sessionModel'

export type ModelSwitchStatus = 'idle' | 'switching' | 'done' | 'failed' | 'deferred' | 'superseded'

export interface ModelSwitchResult {
  status: ModelSwitchStatus
  target?: string
  error?: unknown
}

interface Waiter {
  target: string
  resolve: (result: ModelSwitchResult) => void
}

function _providerOf(target: string): string {
  return target.includes('/') ? target.split('/')[0] : ''
}

function _statusCode(error: unknown): number | undefined {
  if (error && typeof error === 'object') {
    const value = (error as { statusCode?: unknown; status?: unknown }).statusCode
      ?? (error as { status?: unknown }).status
    return typeof value === 'number' ? value : undefined
  }
  return undefined
}

/**
 * Background model switch jobs.
 *
 * Selecting a model on an existing, idle session starts a PATCH immediately so
 * the (potentially slow) summary migration is off the composer path. Jobs are
 * serialized per session and coalesced to the latest target. `wait()` is used by
 * `send` so a turn never races a switch that is still in flight.
 */
export const useModelSwitchStore = defineStore('modelSwitch', () => {
  const sessionModel = useSessionModelStore()
  const statusBySession = ref<Record<string, ModelSwitchStatus>>({})

  const pending = new Map<string, { target: string; serverId: string }>()
  const running = new Map<string, Promise<ModelSwitchResult>>()
  const waiters = new Map<string, Waiter[]>()
  const forgotten = new Set<string>()

  function status(sessionKey: string): ModelSwitchStatus {
    return statusBySession.value[sessionKey] || 'idle'
  }

  function _setStatus(sessionKey: string, next: ModelSwitchStatus): void {
    statusBySession.value = { ...statusBySession.value, [sessionKey]: next }
  }

  function _resolve(sessionKey: string, target: string, result: ModelSwitchResult): void {
    const list = waiters.get(sessionKey)
    if (!list) return
    const remaining: Waiter[] = []
    for (const waiter of list) {
      if (waiter.target === target) waiter.resolve(result)
      else remaining.push(waiter)
    }
    if (remaining.length) waiters.set(sessionKey, remaining)
    else waiters.delete(sessionKey)
  }

  function _register(sessionKey: string, target: string): Promise<ModelSwitchResult> {
    return new Promise(resolve => {
      const list = waiters.get(sessionKey) || []
      list.push({ target, resolve })
      waiters.set(sessionKey, list)
    })
  }

  async function _run(sessionKey: string): Promise<ModelSwitchResult> {
    let last: ModelSwitchResult = { status: 'idle' }
    while (true) {
      const job = pending.get(sessionKey)
      if (!job) break
      pending.delete(sessionKey)
      const { target, serverId } = job

      if (forgotten.has(sessionKey)) {
        last = { status: 'superseded', target }
        _resolve(sessionKey, target, last)
        continue
      }
      if (target === sessionModel.active(sessionKey)) {
        last = { status: 'done', target }
        _setStatus(sessionKey, 'done')
        _resolve(sessionKey, target, last)
        continue
      }

      _setStatus(sessionKey, 'switching')
      try {
        await updateSession(serverId, {
          model: { id: target, providerID: _providerOf(target) },
        }, 120_000)
        if (forgotten.has(sessionKey)) {
          last = { status: 'superseded', target }
        } else {
          sessionModel.setActive(sessionKey, target)
          last = { status: 'done', target }
        }
        _setStatus(sessionKey, last.status)
        _resolve(sessionKey, target, last)
      } catch (error) {
        // 409 = a turn owns the session now; keep desired and let turn-start switch it.
        if (_statusCode(error) === 409) {
          last = { status: 'deferred', target, error }
          _setStatus(sessionKey, 'deferred')
          _resolve(sessionKey, target, last)
          continue
        }
        const stillDesired = !forgotten.has(sessionKey) && sessionModel.desired(sessionKey) === target
        if (stillDesired) sessionModel.revert(sessionKey)
        last = { status: stillDesired ? 'failed' : 'superseded', target, error }
        _setStatus(sessionKey, last.status)
        _resolve(sessionKey, target, last)
      }
    }
    return last
  }

  function _kick(sessionKey: string): void {
    if (running.has(sessionKey)) return
    const run = _run(sessionKey)
    running.set(sessionKey, run)
    const cleanup = () => {
      if (running.get(sessionKey) === run) running.delete(sessionKey)
      if (pending.has(sessionKey)) _kick(sessionKey)
    }
    void run.then(cleanup, cleanup)
  }

  function enqueue(sessionKey: string, target: string, serverId: string = sessionKey): Promise<ModelSwitchResult> {
    if (!sessionKey || sessionKey === SESSION_MODEL_DRAFT_KEY || !target) {
      return Promise.resolve({ status: 'idle', target })
    }
    forgotten.delete(sessionKey)
    const previous = pending.get(sessionKey)
    if (previous && previous.target !== target) {
      _resolve(sessionKey, previous.target, { status: 'superseded', target: previous.target })
    }
    pending.set(sessionKey, { target, serverId: serverId || sessionKey })
    const result = _register(sessionKey, target)
    _kick(sessionKey)
    return result
  }

  async function wait(sessionKey: string): Promise<ModelSwitchResult | null> {
    if (!sessionKey || sessionKey === SESSION_MODEL_DRAFT_KEY) return null
    let last: ModelSwitchResult | null = null
    while (true) {
      if (pending.has(sessionKey) && !running.has(sessionKey)) _kick(sessionKey)
      const run = running.get(sessionKey)
      if (!run) {
        if (pending.has(sessionKey)) continue
        return last
      }
      last = await run
      if (!running.has(sessionKey) && !pending.has(sessionKey)) return last
    }
  }

  function forget(sessionKey: string): void {
    if (!sessionKey) return
    forgotten.add(sessionKey)
    const job = pending.get(sessionKey)
    pending.delete(sessionKey)
    if (job) _resolve(sessionKey, job.target, { status: 'superseded', target: job.target })
    if (sessionKey in statusBySession.value) {
      const next = { ...statusBySession.value }
      delete next[sessionKey]
      statusBySession.value = next
    }
  }

  return {
    statusBySession,
    status,
    enqueue,
    wait,
    forget,
  }
})
