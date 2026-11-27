import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  getSession,
  getSessionPlan,
  listForms,
  listInbox,
  listMessages,
  listPermissions,
  prompt as promptApi,
  replaySessionEvents,
} from '../api/v2'
import type {
  InboxInfo,
  PromptInput,
  PlanUpdatedData,
  SessionInfoV2,
  UserMessage,
  V2Event,
} from '../contracts/v2'
import { getV2EventStream } from './eventStream'
import {
  applyV2Event,
  emptyRuntime,
  eventSessionId,
  type LivePlan,
  type V2SessionRuntime,
} from './runtime'

const ERROR_DISMISS_MS = 15_000
const errorDismissTimers = new Map<string, ReturnType<typeof setTimeout>>()

function _livePlan(plan: PlanUpdatedData): LivePlan {
  return {
    plan_id: plan.plan_id,
    revision: plan.revision,
    tier: plan.tier ?? null,
    steps: plan.steps.map(step => ({ ...step })),
  }
}

export const useV2SessionStore = defineStore('v2Session', () => {
  const runtimes = ref<Record<string, V2SessionRuntime>>({})
  const activeSessionId = ref<string | null>(null)
  const hydrating = new Set<string>()
  const buffers = new Map<string, V2Event[]>()
  let started = false
  let unsubscribeEvent: (() => void) | null = null
  let unsubscribeConnected: (() => void) | null = null

  function ensureRuntime(sessionId: string): V2SessionRuntime {
    let runtime = runtimes.value[sessionId]
    if (!runtime) {
      runtime = emptyRuntime(sessionId)
      runtimes.value[sessionId] = runtime
    }
    return runtime
  }

  function getRuntime(sessionId: string): V2SessionRuntime | undefined {
    return runtimes.value[sessionId]
  }

  function clearError(sessionId: string): void {
    const timer = errorDismissTimers.get(sessionId)
    if (timer) {
      clearTimeout(timer)
      errorDismissTimers.delete(sessionId)
    }
    const runtime = runtimes.value[sessionId]
    if (runtime?.error) setRuntime({ ...runtime, error: null })
  }

  function scheduleErrorDismiss(sessionId: string): void {
    const existing = errorDismissTimers.get(sessionId)
    if (existing) clearTimeout(existing)
    errorDismissTimers.set(sessionId, setTimeout(() => {
      errorDismissTimers.delete(sessionId)
      clearError(sessionId)
    }, ERROR_DISMISS_MS))
  }

  function setRuntime(runtime: V2SessionRuntime): void {
    runtimes.value[runtime.id] = runtime
  }

  function apply(event: V2Event): void {
    const sessionId = eventSessionId(event)
    if (!sessionId) return
    if (event.type === 'session.execution.failed') scheduleErrorDismiss(sessionId)
    const runtime = ensureRuntime(sessionId)
    if (!runtime.hydrated) {
      const buffer = buffers.get(sessionId) ?? []
      buffer.push(event)
      buffers.set(sessionId, buffer)
      if (!hydrating.has(sessionId)) void hydrate(sessionId)
      return
    }
    setRuntime(applyV2Event(runtime, event))
    if (
      event.type === 'session.execution.succeeded' ||
      event.type === 'session.execution.failed' ||
      event.type === 'session.execution.interrupted' ||
      event.type === 'session.compaction.ended' ||
      event.type === 'session.compaction.failed'
    ) {
      void hydrate(sessionId)
    }
  }

  async function hydrate(sessionId: string): Promise<V2SessionRuntime | undefined> {
    if (hydrating.has(sessionId)) return runtimes.value[sessionId]
    hydrating.add(sessionId)
    buffers.set(sessionId, [])
    try {
      const previous = runtimes.value[sessionId]
      const cursor = previous?.lastDurableSeq ?? 0
      const [info, messages, inbox, permissions, forms, plan, replayed] = await Promise.all([
        getSession(sessionId),
        listMessages(sessionId, { limit: 500, order: 'asc' }),
        listInbox(sessionId),
        listPermissions(sessionId).catch(() => []),
        listForms(sessionId, 'pending').catch(() => []),
        getSessionPlan(sessionId).catch(() => null),
        cursor > 0
          ? replaySessionEvents(sessionId, cursor).catch(() => [])
          : Promise.resolve([] as V2Event[]),
      ])
      const resolvedForms = previous ? { ...previous.resolvedForms } : {}
      const base: V2SessionRuntime = {
        ...emptyRuntime(sessionId),
        info,
        status: { type: info.status },
        messages: messages.data,
        inbox,
        permissions,
        forms: forms.filter(
          (form) => form.state === 'pending' && (resolvedForms[form.id] ?? -1) < form.revision,
        ),
        plan: info.status === 'busy' || info.status === 'retry'
          ? (plan ? _livePlan(plan) : null)
          : null,
        hydrated: true,
        seenEventIds: previous?.seenEventIds.slice() ?? [],
        resolvedForms,
        lastDurableSeq: cursor,
      }
      base.error = previous?.error ?? null
      if (info.status === 'busy' || info.status === 'retry') {
        base.activeAssistantId = previous?.activeAssistantId ?? null
        base.compaction = previous?.compaction ?? null
      }
      let next = base
      for (const event of replayed) next = applyV2Event(next, event)
      const buffered = buffers.get(sessionId) ?? []
      for (const event of buffered) next = applyV2Event(next, event)
      setRuntime(next)
      return next
    } catch {
      const runtime = runtimes.value[sessionId]
      if (runtime) setRuntime({ ...runtime, hydrated: runtime.hydrated })
      return runtime
    } finally {
      buffers.delete(sessionId)
      hydrating.delete(sessionId)
    }
  }

  async function hydrateAllKnown(): Promise<void> {
    const ids = Object.keys(runtimes.value)
    await Promise.all(ids.map((id) => hydrate(id)))
  }

  function start(): void {
    if (started) return
    started = true
    const stream = getV2EventStream()
    unsubscribeEvent = stream.subscribe((event) => apply(event))
    unsubscribeConnected = stream.onConnected(() => {
      void hydrateAllKnown()
    })
    stream.start()
  }

  function stop(): void {
    started = false
    unsubscribeEvent?.()
    unsubscribeConnected?.()
    unsubscribeEvent = null
    unsubscribeConnected = null
    for (const timer of errorDismissTimers.values()) clearTimeout(timer)
    errorDismissTimers.clear()
    getV2EventStream().stop()
  }

  async function send(sessionId: string, body: PromptInput): Promise<InboxInfo> {
    const item = await promptApi(sessionId, body)
    const runtime = ensureRuntime(sessionId)
    // 立即把用户消息放进时间线（与历史消息同一套渲染），否则发送后到回合结束
    // hydration 之前看不到自己这条消息，样式也与上方的用户消息不一致。
    const messages = runtime.messages.slice()
    if (item.item.type === 'user' && !messages.some((msg) => msg.id === item.id)) {
      const payload = item.item.payload
      const user: UserMessage = {
        type: 'user',
        id: item.id,
        time_created: item.time_created || Date.now(),
        text: body.text || payload.text,
        files: body.files ?? payload.files,
        agents: body.agents ?? payload.agents,
        skills: body.skills ?? payload.skills,
        metadata: body.metadata ?? payload.metadata,
        voice: body.voice ?? null,
      }
      messages.push(user)
    }
    setRuntime({ ...runtime, messages })
    return item
  }

  function setInbox(sessionId: string, item: InboxInfo): void {
    const runtime = ensureRuntime(sessionId)
    const index = runtime.inbox.findIndex((entry) => entry.id === item.id)
    const inbox = runtime.inbox.slice()
    if (index >= 0) inbox[index] = item
    else inbox.push(item)
    setRuntime({ ...runtime, inbox })
  }

  function removeInbox(sessionId: string, inboxId: string): void {
    const runtime = ensureRuntime(sessionId)
    setRuntime({
      ...runtime,
      inbox: runtime.inbox.filter((entry) => entry.id !== inboxId),
      messages: runtime.messages.filter((message) => message.id !== inboxId),
    })
  }

  function removeForm(sessionId: string, formId: string, revision: number = 0): void {
    const runtime = ensureRuntime(sessionId)
    setRuntime({
      ...runtime,
      forms: runtime.forms.filter((form) => form.id !== formId),
      resolvedForms: {
        ...runtime.resolvedForms,
        [formId]: Math.max(runtime.resolvedForms[formId] ?? -1, revision),
      },
    })
  }

  function setInfo(sessionId: string, info: SessionInfoV2): void {
    const runtime = ensureRuntime(sessionId)
    setRuntime({ ...runtime, info, status: { type: info.status } })
  }

  function setActive(sessionId: string | null): void {
    activeSessionId.value = sessionId
  }

  return {
    runtimes,
    activeSessionId,
    ensureRuntime,
    getRuntime,
    clearError,
    apply,
    hydrate,
    hydrateAllKnown,
    start,
    stop,
    send,
    setInbox,
    removeInbox,
    removeForm,
    setInfo,
    setActive,
  }
})
