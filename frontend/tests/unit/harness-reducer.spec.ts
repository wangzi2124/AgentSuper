import { describe, expect, it } from 'vitest'
import { adaptLegacyEvent } from '../../src/harness/adapter'
import { applyHarnessEvent, createTurnState } from '../../src/harness/reducer'

describe('harness reducer', () => {
  it('deduplicates seq and ignores stale turns', () => {
    let state = createTurnState('t1')
    state = applyHarnessEvent(state, adaptLegacyEvent({ type: 'text_delta', agent_id: '', delta: 'a', turn_id: 't1', seq: 1 } as any))
    state = applyHarnessEvent(state, adaptLegacyEvent({ type: 'text_delta', agent_id: '', delta: 'b', turn_id: 't1', seq: 2 } as any))
    state = applyHarnessEvent(state, adaptLegacyEvent({ type: 'text_delta', agent_id: '', delta: 'dup', turn_id: 't1', seq: 2 } as any))
    state = applyHarnessEvent(state, adaptLegacyEvent({ type: 'text_delta', agent_id: '', delta: 'old', turn_id: 't0', seq: 3 } as any))
    expect(state.assistantContent).toBe('ab')
    expect(state.lastSeq).toBe(2)
  })

  it('upserts agent steps by step_id and keeps parts', () => {
    let state = createTurnState('t1')
    state = applyHarnessEvent(state, adaptLegacyEvent({
      type: 'agent_start', agent_id: 'build', agent_name: 'build', turn_id: 't1', seq: 1,
    } as any))
    state = applyHarnessEvent(state, adaptLegacyEvent({
      type: 'agent_step', agent_id: 'build', turn_id: 't1', seq: 2,
      step: { type: 'tool_start', step_id: 's1', name: 'read', status: 'running' },
    } as any))
    state = applyHarnessEvent(state, adaptLegacyEvent({
      type: 'agent_step', agent_id: 'build', turn_id: 't1', seq: 3,
      step: { type: 'tool_end', step_id: 's1', name: 'read', status: 'completed', tool_result: 'ok' },
    } as any))
    const agent = state.agents.build
    expect(agent.steps).toHaveLength(1)
    expect(agent.steps[0].status).toBe('completed')
    expect(agent.parts?.filter(p => p.kind === 'tool')).toHaveLength(1)
  })

  it('maps permission/error/terminal events', () => {
    let state = createTurnState('t1')
    state = applyHarnessEvent(state, adaptLegacyEvent({
      type: 'permission_request', agent_id: 'build', turn_id: 't1', seq: 1,
      request_id: 'p1', path: 'a.txt', operation: 'write', tool_name: 'write_file',
    } as any))
    expect(state.pendingPermissions[0].id).toBe('p1')

    state = applyHarnessEvent(state, adaptLegacyEvent({
      type: 'error', agent_id: '', turn_id: 't1', seq: 2,
      error: 'rate limited', retryable: true, status_code: 429, error_type: 'rate_limit',
    } as any))
    expect(state.phase).toBe('error')
    expect(state.error?.retryable).toBe(true)
    expect(state.error?.statusCode).toBe(429)

    state = applyHarnessEvent(state, adaptLegacyEvent({
      type: 'done', agent_id: '', turn_id: 't1', seq: 3, answer: 'final', model: 'm',
    } as any))
    expect(state.phase).toBe('done')
    expect(state.answer).toBe('final')
  })

  it('adapts legacy events without turn_id to the current turn', () => {
    const event = adaptLegacyEvent({ type: 'text_delta', agent_id: '', delta: 'x' } as any, 'turn-local')
    expect(event.turn_id).toBe('turn-local')
    expect(event.seq).toBeUndefined()
  })
})
