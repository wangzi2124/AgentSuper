import type { MultiAgentSSEEvent } from '../types'
import type { HarnessEvent } from './events'

/**
 * Normalize legacy SSE payloads into the Harness event shape.
 * Events emitted before `turn_id`/`seq` existed are attached to the current turn
 * and keep `seq` undefined; the reducer then falls back to arrival order.
 */
export function adaptLegacyEvent(
  raw: MultiAgentSSEEvent,
  fallbackTurnId?: string,
): HarnessEvent {
  const event = raw as MultiAgentSSEEvent & {
    turn_id?: string
    session_id?: string
    seq?: number
    error_code?: string
  }
  return {
    ...event,
    turn_id: event.turn_id || fallbackTurnId,
    seq: typeof event.seq === 'number' ? event.seq : undefined,
  } as HarnessEvent
}
