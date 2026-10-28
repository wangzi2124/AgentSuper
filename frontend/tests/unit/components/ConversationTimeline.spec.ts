import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationTimeline from '@/components/ConversationTimeline.vue'
import type { MultiAgentMessage } from '@/types'

function message(): MultiAgentMessage {
  return {
    id: 'm1',
    role: 'assistant',
    content: 'final answer',
    timestamp: new Date(),
    agents: [
      {
        agent_id: 'build',
        agent_name: 'build',
        status: 'completed',
        content: '',
        steps: [],
        parts: [
          {
            seq: 0,
            kind: 'tool',
            step: { type: 'step_end', step_id: 's1', name: 'plan', status: 'completed', detail: '{"task":"plan"}' },
          },
          {
            seq: 1,
            kind: 'tool',
            step: {
              type: 'tool_end',
              step_id: 's2',
              name: 'read_file',
              tool_name: 'read_file',
              status: 'completed',
              tool_args: { path: 'a.txt' },
              tool_result: 'ORION-42',
            },
          },
          {
            seq: 2,
            kind: 'notice',
            notice: {
              model: 'ollama_chat/x',
              window: 4096,
              budget: 2745,
              reserve: 1024,
              estimated_before: 5000,
              estimated_after: 2500,
              dropped_messages: 6,
            },
          },
          { seq: 3, kind: 'text', text: 'I read the file.' },
        ],
      },
    ],
  }
}

describe('ConversationTimeline', () => {
  it('shows real wall-clock duration from message.duration_ms', () => {
    const wrapper = mount(ConversationTimeline, {
      props: { message: { ...message(), duration_ms: 4200 }, routingStatus: '', isLast: true, running: false },
    })
    expect(wrapper.text()).toContain('Completed · 4.2s')
  })

  it('does not fake 1s when duration is unknown', () => {
    const m = message()
    m.agents[0].steps = []
    m.agents[0].parts = [{ seq: 0, kind: 'text', text: 'hi' }]
    const wrapper = mount(ConversationTimeline, {
      props: { message: m, routingStatus: '', isLast: true, running: false },
    })
    const text = wrapper.text()
    expect(text).toContain('Completed')
    expect(text).not.toContain('Completed · 1s')
  })

  it('hides internal graph nodes and keeps real tools + text', () => {
    const wrapper = mount(ConversationTimeline, {
      props: {
        message: message(),
        routingStatus: '???????',
        isLast: true,
        running: true,
      },
    })
    const text = wrapper.text()
    expect(text).not.toContain('????')
    expect(text).not.toContain('????')
    expect(text).not.toContain('plan')
    expect(text).toContain('read_file')
    expect(text).toContain('ORION-42')
    expect(text).toContain('I read the file.')
    expect(text).toContain('上下文已裁剪')
    expect(text).toContain('5000 → 2500 tokens')
    expect(text).toContain('final answer')
    expect(wrapper.find('.agent-chip').exists()).toBe(false)
  })
})
