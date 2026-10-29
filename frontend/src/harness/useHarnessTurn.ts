import { ref, type Ref } from 'vue'
import { sendMultiAgentStream } from '../api/multiAgent'
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

export interface HarnessTurn {
  state: Ref<TurnState>
  run: (request: MultiAgentChatRequest, options?: HarnessRunOptions) => Promise<void>
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

  async function run(
    request: MultiAgentChatRequest,
    options: HarnessRunOptions = {},
  ): Promise<void> {
    reset(state.value.turnId, state.value.sessionId)
    const active = new AbortController()
    controller = active
    const signal = options.signal || active.signal
    const onSessionId = (sessionId: string) => {
      state.value = { ...state.value, sessionId }
      options.onSessionId?.(sessionId)
    }
    const coalescer = createTurnEventCoalescer({
      onEvent: (event) => {
        const next = applyHarnessEvent(state.value, event)
        if (next === state.value) return
        state.value = next
        options.onEvent?.(event, next)
      },
    })
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

  return { state, run, abort, reset }
}
