import { ref, type Ref } from 'vue'
import { resumeMultiAgentStream, sendMultiAgentStream } from '../api/multiAgent'
import type { MultiAgentChatRequest, MultiAgentSSEEvent } from '../types'
import { adaptLegacyEvent } from './adapter'
import { applyHarnessEvent, createTurnState } from './reducer'
import { createTurnEventCoalescer } from './useTimelineStream'
import type { HarnessEvent, TurnState } from './events'

export interface HarnessRunOptions {
  signal?: AbortSignal
  onEvent?: (event: HarnessEvent, state: TurnState) => void
  onSessionId?: (sessionId: string) => void
}

export interface HarnessResumeTarget {
  turn_id: string
  /** 仅用于展示；续接始终从头重放，靠 `turn_id + seq` 去重重建状态。 */
  last_seq?: number
}

export interface HarnessTurn {
  state: Ref<TurnState>
  run: (request: MultiAgentChatRequest, options?: HarnessRunOptions) => Promise<void>
  resume: (
    sessionId: string,
    target: HarnessResumeTarget,
    options?: HarnessRunOptions,
  ) => Promise<void>
  abort: () => void
  reset: (turnId?: string, sessionId?: string) => void
}

/**
 * Owns one frontend turn: transport lifecycle + event normalization + reducer.
 * The surrounding store remains responsible for sessions, queues and persistence.
 */
export function useHarnessTurn(): HarnessTurn {
  const state = ref(createTurnState())
  let controller: AbortController | null = null

  function reset(turnId?: string, sessionId?: string): void {
    state.value = createTurnState(turnId || `turn-${Date.now()}`, sessionId)
  }

  function abort(): void {
    controller?.abort()
  }

  function makeCoalescer(options: HarnessRunOptions) {
    return createTurnEventCoalescer({
      onEvent: (event) => {
        const next = applyHarnessEvent(state.value, event)
        if (next === state.value) return
        state.value = next
        options.onEvent?.(event, next)
      },
    })
  }

  async function run(
    request: MultiAgentChatRequest,
    options: HarnessRunOptions = {},
  ): Promise<void> {
    // 每轮都是新 turn：初始 id 用 'local'，服务端事件携带真实 turn_id 后自动收敛。
    reset('local', state.value.sessionId)
    const active = new AbortController()
    controller = active
    const signal = options.signal || active.signal
    const onSessionId = (sessionId: string) => {
      state.value = { ...state.value, sessionId }
      options.onSessionId?.(sessionId)
    }
    const coalescer = makeCoalescer(options)
    try {
      await sendMultiAgentStream(
        request,
        (raw: MultiAgentSSEEvent) => {
          coalescer.push(adaptLegacyEvent(raw, state.value.turnId))
        },
        signal,
        onSessionId,
      )
    } finally {
      // 终态/断流前把缓冲区里的最后一段文本落盘，避免答案尾部丢失。
      coalescer.flush()
      coalescer.dispose()
      if (controller === active) controller = null
    }
  }

  /**
   * 续接后台仍在执行的回合：固定 `turn_id` 后从头重放，
   * 利用 `turn_id + seq` 去重重建（断连期间漏掉的事件也会补齐）。
   */
  async function resume(
    sessionId: string,
    target: HarnessResumeTarget,
    options: HarnessRunOptions = {},
  ): Promise<void> {
    reset(target.turn_id, sessionId)
    state.value = { ...state.value, phase: 'running' }
    const active = new AbortController()
    controller = active
    const signal = options.signal || active.signal
    const coalescer = makeCoalescer(options)
    try {
      await resumeMultiAgentStream(
        sessionId,
        0,
        (raw: MultiAgentSSEEvent) => {
          coalescer.push(adaptLegacyEvent(raw, target.turn_id))
        },
        signal,
      )
    } finally {
      coalescer.flush()
      coalescer.dispose()
      if (controller === active) controller = null
    }
  }

  return { state, run, resume, abort, reset }
}
