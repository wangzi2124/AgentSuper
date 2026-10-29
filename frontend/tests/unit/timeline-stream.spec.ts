import { describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import type { MultiAgentMessage } from '../../src/types'
import {
  createTurnEventCoalescer,
  useStickToBottom,
  useTimelineItems,
} from '../../src/harness/useTimelineStream'
import { findUnclosedFenceStart, splitUnclosedFence } from '../../src/utils/markdownFence'

function delta(agentId: string, seq: number, text: string) {
  return { type: 'text_delta', agent_id: agentId, delta: text, turn_id: 't1', seq } as any
}

describe('turn event coalescer', () => {
  it('merges text deltas per agent within the flush window', () => {
    vi.useFakeTimers()
    try {
      const seen: any[] = []
      const coalescer = createTurnEventCoalescer({ onEvent: e => seen.push(e), flushMs: 100 })
      coalescer.push(delta('build', 1, 'he'))
      coalescer.push(delta('build', 2, 'llo'))
      expect(seen).toHaveLength(0)
      vi.advanceTimersByTime(100)
      expect(seen).toHaveLength(1)
      expect(seen[0].delta).toBe('hello')
      expect(seen[0].seq).toBe(2)
      coalescer.dispose()
    } finally {
      vi.useRealTimers()
    }
  })

  it('flushes pending text before non-text events and keeps seq order', () => {
    const seen: any[] = []
    const coalescer = createTurnEventCoalescer({ onEvent: e => seen.push(e), flushMs: 100 })
    coalescer.push(delta('build', 5, 'abc'))
    coalescer.push({ type: 'agent_step', agent_id: 'build', turn_id: 't1', seq: 6, step: { step_id: 's1' } } as any)
    expect(seen.map(e => e.type)).toEqual(['text_delta', 'agent_step'])
    expect(seen[0].delta).toBe('abc')
    expect(seen[0].seq).toBe(5)
    coalescer.dispose()
  })

  it('keeps interleaved agents in separate groups', () => {
    const seen: any[] = []
    const coalescer = createTurnEventCoalescer({ onEvent: e => seen.push(e), flushMs: 100 })
    coalescer.push(delta('a', 1, 'A1'))
    coalescer.push(delta('b', 2, 'B1'))
    coalescer.push(delta('a', 3, 'A2'))
    coalescer.flush()
    expect(seen).toHaveLength(2)
    // 按 seq 升序回放，避免 reducer 去重丢掉后到的组。
    expect(seen.map(e => e.agent_id)).toEqual(['b', 'a'])
    expect(seen[0].delta).toBe('B1')
    expect(seen[1].delta).toBe('A1A2')
    coalescer.dispose()
  })

  it('flushMs=0 emits immediately', () => {
    const seen: any[] = []
    const coalescer = createTurnEventCoalescer({ onEvent: e => seen.push(e), flushMs: 0 })
    coalescer.push(delta('build', 1, 'x'))
    expect(seen).toHaveLength(1)
    expect(seen[0].delta).toBe('x')
    coalescer.dispose()
  })
})

describe('markdown fence detection', () => {
  it('returns -1 when all fences are closed', () => {
    const src = 'intro\n```ts\nconst a = 1\n```\noutro'
    expect(findUnclosedFenceStart(src)).toBe(-1)
  })

  it('finds an unclosed fence start', () => {
    const src = 'intro\n```ts\nconst a = 1'
    const start = findUnclosedFenceStart(src)
    expect(start).toBe('intro\n'.length)
    expect(splitUnclosedFence(src)?.tail).toBe('```ts\nconst a = 1')
  })

  it('supports tilde fences and ignores shorter closers', () => {
    expect(findUnclosedFenceStart('~~~\ncode')).toBe(0)
    expect(findUnclosedFenceStart('````\n```\n````')).toBe(-1)
  })

  it('returns null when closed', () => {
    expect(splitUnclosedFence('a\n```\nb\n```')).toBeNull()
  })
})

function messageWith(agents: MultiAgentMessage['agents'], content: string): MultiAgentMessage {
  return {
    id: 'm1',
    role: 'assistant',
    content,
    agents,
    timestamp: new Date(),
  } as MultiAgentMessage
}

describe('timeline items', () => {
  it('detects the final answer already present in the timeline', () => {
    const msg = messageWith([
      {
        agent_id: 'build',
        agent_name: 'build',
        status: 'completed',
        content: 'hello world',
        steps: [],
        parts: [{ seq: 0, kind: 'text', text: 'hello world' }],
      },
    ], 'hello world')
    const { items, isDuplicateAnswer } = useTimelineItems(() => msg)
    expect(items.value).toHaveLength(1)
    expect(isDuplicateAnswer.value).toBe(true)
  })

  it('ignores reasoning parts when comparing the final answer', () => {
    const msg = messageWith([
      {
        agent_id: 'build',
        agent_name: 'build',
        status: 'completed',
        content: 'answer',
        steps: [],
        parts: [
          { seq: 0, kind: 'reasoning', text: 'thinking' },
          { seq: 1, kind: 'text', text: 'answer' },
        ],
      },
    ], 'answer')
    const { isDuplicateAnswer } = useTimelineItems(() => msg)
    expect(isDuplicateAnswer.value).toBe(true)
  })

  it('deduplicates multi-agent timelines too', () => {
    const msg = messageWith([
      {
        agent_id: 'a', agent_name: 'a', status: 'completed', content: 'part one ', steps: [],
        parts: [{ seq: 0, kind: 'text', text: 'part one ' }],
      },
      {
        agent_id: 'b', agent_name: 'b', status: 'completed', content: 'part two', steps: [],
        parts: [{ seq: 0, kind: 'text', text: 'part two' }],
      },
    ], 'part one part two')
    const { isDuplicateAnswer } = useTimelineItems(() => msg)
    expect(isDuplicateAnswer.value).toBe(true)
  })
})

describe('stick to bottom', () => {
  it('follows with instant scroll only while near the bottom', async () => {
    const scrollTo = vi.fn()
    const el = ref<any>({ scrollHeight: 1000, scrollTop: 900, clientHeight: 100, scrollTo })
    const source = ref(0)
    const { isNearBottom, onScroll } = useStickToBottom(el, () => source.value)

    source.value += 1
    await nextTick()
    await nextTick()
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 1000, behavior: 'auto' })

    el.value = { scrollHeight: 1000, scrollTop: 200, clientHeight: 100, scrollTo }
    onScroll()
    expect(isNearBottom.value).toBe(false)

    scrollTo.mockClear()
    source.value += 1
    await nextTick()
    await nextTick()
    expect(scrollTo).not.toHaveBeenCalled()
  })
})
