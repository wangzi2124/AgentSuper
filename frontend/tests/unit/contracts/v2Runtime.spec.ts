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

  it('tracks the running assistant message and clears it when the turn ends', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.execution.started', { session_id: 's1' }))
    runtime = applyV2Event(runtime, evt('session.step.started', {
      session_id: 's1', assistant_message_id: 'a1', agent: null, model: null, started: 1,
    }))
    expect(runtime.activeAssistantId).toBe('a1')
    runtime = applyV2Event(runtime, evt('session.execution.succeeded', { session_id: 's1' }))
    expect(runtime.activeAssistantId).toBeNull()
  })

  it('tracks live compaction progress during the turn', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.execution.started', { session_id: 's1' }))
    runtime = applyV2Event(runtime, evt('session.compaction.started', {
      session_id: 's1', reason: 'auto',
    }))
    runtime = applyV2Event(runtime, evt('session.compaction.delta', {
      session_id: 's1', text: '摘要',
    }))
    expect(runtime.compaction).toMatchObject({ status: 'running', text: '摘要' })
    runtime = applyV2Event(runtime, evt('session.compaction.ended', {
      session_id: 's1', reason: 'auto', text: '摘要完成',
    }))
    expect(runtime.compaction).toMatchObject({ status: 'completed', text: '摘要完成' })
    runtime = applyV2Event(runtime, evt('session.execution.succeeded', { session_id: 's1' }))
    expect(runtime.compaction).toBeNull()
  })

  it('stores the latest plan snapshot and ignores stale revisions', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.plan.updated', {
      session_id: 's1',
      plan_id: 'p1',
      revision: 1,
      tier: 'serial',
      steps: [
        { id: 't1', title: '读取文件', status: 'pending' },
        { id: 't2', title: '修改文件', status: 'pending' },
      ],
    }))
    expect(runtime.plan).toMatchObject({ plan_id: 'p1', revision: 1, tier: 'serial' })
    expect(runtime.plan?.steps.map(step => step.title)).toEqual(['读取文件', '修改文件'])

    runtime = applyV2Event(runtime, evt('session.plan.updated', {
      session_id: 's1',
      plan_id: 'p1',
      revision: 1,
      tier: 'serial',
      steps: [{ id: 't1', title: '旧快照', status: 'completed' }],
    }))
    expect(runtime.plan?.steps[0]?.title).toBe('读取文件')

    runtime = applyV2Event(runtime, evt('session.plan.updated', {
      session_id: 's1',
      plan_id: 'p1',
      revision: 2,
      tier: 'serial',
      steps: [
        { id: 't1', title: '读取文件', status: 'completed' },
        { id: 't2', title: '修改文件', status: 'in_progress' },
      ],
    }))
    expect(runtime.plan?.revision).toBe(2)
    expect(runtime.plan?.steps[1]?.status).toBe('in_progress')

    runtime = applyV2Event(runtime, evt('session.execution.started', { session_id: 's1' }))
    expect(runtime.plan).toBeNull()
  })

  it('drops the optimistic user message when its inbox item is cancelled', () => {
    const runtime = emptyRuntime('s1')
    runtime.messages.push({ type: 'user', id: 'in1', time_created: 1, text: 'hi' })
    const next = applyV2Event(runtime, evt('session.inbox.cancelled', {
      session_id: 's1', inbox_id: 'in1',
    }))
    expect(next.messages.find(message => message.id === 'in1')).toBeUndefined()
  })

  it('upserts a structured question form and removes it on cancel', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('form.created', {
      session_id: 's1',
      id: 'form_q',
      kind: 'question',
      title: '下一步怎么做？',
      header: '实现方向',
      question: '下一步怎么做？',
      state: 'pending',
      response: {},
      fields: [{
        id: 'answer',
        type: 'select',
        label: '下一步怎么做？',
        required: true,
        options: ['先补测试', '直接实现'],
        option_details: [
          { label: '先补测试', description: '先写契约测试', recommended: true },
          { label: '直接实现', description: '立即写代码', recommended: false },
        ],
        multiple: false,
        allow_custom: true,
      }],
    }))
    expect(runtime.forms[0]?.fields[0]?.option_details?.[0]?.recommended).toBe(true)
    runtime = applyV2Event(runtime, evt('form.cancelled', {
      session_id: 's1', form_id: 'form_q',
    }))
    expect(runtime.forms).toHaveLength(0)
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

  it('surfaces action-not-executed warnings with stable ids', () => {
    let runtime = emptyRuntime('s1')
    runtime = applyV2Event(runtime, evt('session.synthetic', {
      session_id: 's1',
      text: '我还没有实际执行写入/修改/删除等工具调用，无法确认操作已完成。',
      description: 'action_not_executed',
      metadata: { warning_id: 'a1_action_warning' },
    }, 'evt_1'))
    const warning = runtime.messages[0]
    expect(warning?.type).toBe('synthetic')
    expect(warning?.id).toBe('a1_action_warning')

    runtime = applyV2Event(runtime, evt('session.synthetic', {
      session_id: 's1',
      text: '我还没有实际执行写入/修改/删除等工具调用，无法确认操作已完成。',
      description: 'action_not_executed',
      metadata: { warning_id: 'a1_action_warning' },
    }, 'evt_2'))
    expect(runtime.messages).toHaveLength(1)
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
