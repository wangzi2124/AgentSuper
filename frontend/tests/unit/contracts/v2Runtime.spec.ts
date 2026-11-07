import { describe, expect, it } from 'vitest'

import type { V2Event } from '../../../src/contracts/v2'
import { parseSseBuffer } from '../../../src/v2/eventStream'
import { applyV2Event, emptyRuntime } from '../../../src/v2/runtime'

let seq = 0
function evt(type: string, data: Record<string, unknown>, id?: string): V2Event {
  seq += 1
  return {
    id: id ?? `e${seq}`,
    created: 1762500000000 + seq,
    type,
    data,
  } as unknown as V2Event
}

describe('v2 runtime reducer', () => {
  it('assembles text parts from started/delta/ended', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.text.started', {
      session_id: 's1', assistant_message_id: 'a1', ordinal: 0,
    }))
    runtime = applyV2Event(runtime, evt('session.text.delta', {
      session_id: 's1', assistant_message_id: 'a1', ordinal: 0, delta: 'hel',
    }))
    runtime = applyV2Event(runtime, evt('session.text.delta', {
      session_id: 's1', assistant_message_id: 'a1', ordinal: 0, delta: 'lo',
    }))
    runtime = applyV2Event(runtime, evt('session.text.ended', {
      session_id: 's1', assistant_message_id: 'a1', ordinal: 0, text: 'hello',
    }))
    const assistant = runtime.messages[0]
    expect(assistant?.type).toBe('assistant')
    if (assistant?.type === 'assistant') {
      expect(assistant.content).toEqual([{ type: 'text', text: 'hello', ordinal: 0 }])
    }
  })

  it('ignores duplicate event ids', () => {
    const event = evt('session.text.delta', {
      session_id: 's1', assistant_message_id: 'a1', ordinal: 0, delta: 'x',
    }, 'dup')
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, event)
    const once = runtime.messages[0]
    runtime = applyV2Event(runtime, event)
    expect(runtime.messages[0]).toBe(once)
  })

  it('updates tool lifecycle', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.tool.input.started', {
      session_id: 's1', assistant_message_id: 'a1', id: 't1', name: 'ls',
    }))
    runtime = applyV2Event(runtime, evt('session.tool.called', {
      session_id: 's1', assistant_message_id: 'a1', id: 't1', input: { path: '.' }, executed: true,
    }))
    runtime = applyV2Event(runtime, evt('session.tool.success', {
      session_id: 's1', assistant_message_id: 'a1', id: 't1',
      content: [{ type: 'text', text: 'ok' }], metadata: {}, executed: true,
    }))
    const assistant = runtime.messages[0]
    if (assistant?.type === 'assistant') {
      const tool = assistant.content.find(part => part.type === 'tool')
      expect(tool?.state.status).toBe('completed')
    }
  })

  it('tracks inbox and status', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.execution.started', { session_id: 's1' }))
    expect(runtime.status.type).toBe('busy')
    runtime = applyV2Event(runtime, evt('session.idle', { session_id: 's1', outcome: 'succeeded' }))
    expect(runtime.status.type).toBe('idle')
  })

  it('drops the optimistic user message when its inbox item is cancelled', () => {
    const runtime = emptyRuntime('s1')
    runtime.messages.push({ type: 'user', id: 'in1', time_created: 1, text: 'hi' })
    const next = applyV2Event(runtime, evt('session.inbox.cancelled', {
      session_id: 's1', inbox_id: 'in1',
    }))
    expect(next.messages.find(message => message.id === 'in1')).toBeUndefined()
  })

  it('surfaces execution failures and clears them on the next run', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.execution.failed', {
      session_id: 's1',
      error: { type: 'execution_error', message: 'LLM Provider NOT provided' },
    }))
    expect(runtime.status.type).toBe('idle')
    expect(runtime.error?.message).toBe('LLM Provider NOT provided')
    runtime = applyV2Event(runtime, evt('session.execution.started', { session_id: 's1' }))
    expect(runtime.error).toBeNull()
  })
})

describe('v2 SSE parser', () => {
  it('parses multiple frames and keeps the remainder', () => {
    const text = [
      'id: 1',
      'event: server.connected',
      'data: {"type":"server.connected"}',
      '',
      'id: 2',
      'event: session.idle',
      'data: {"type":"session.idle"}',
      '',
      'id: 3',
    ].join('\n')
    const { frames, rest } = parseSseBuffer(text)
    expect(frames).toHaveLength(2)
    expect(frames[0]?.event).toBe('server.connected')
    expect(frames[1]?.id).toBe('2')
    expect(rest).toContain('id: 3')
  })
})
