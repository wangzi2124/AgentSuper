import { computed, nextTick, ref, watch, type Ref } from 'vue'
import type { AgentOutputPart, AgentStep, MultiAgentMessage } from '../types'
import { normalizeAgent } from '../utils/agentDisplay'
import type { HarnessEvent } from './events'

/** 流式文本合并窗口：约 10fps，兼顾流式观感与主线程占用。 */
export const STREAM_FLUSH_MS = 100

export interface TurnEventCoalescer {
  push: (event: HarnessEvent) => void
  flush: () => void
  dispose: () => void
}

export interface TurnEventCoalescerOptions {
  onEvent: (event: HarnessEvent) => void
  flushMs?: number
}

interface PendingText {
  key: string
  event: HarnessEvent
  delta: string
}

/**
 * 合并 text_delta，降低 reducer / watcher / Markdown 渲染频率。
 * 非文本事件到来前强制 flush，保证 part 顺序与 seq 单调。
 */
export function createTurnEventCoalescer(
  options: TurnEventCoalescerOptions,
): TurnEventCoalescer {
  const flushMs = Math.max(0, options.flushMs ?? STREAM_FLUSH_MS)
  let pending: PendingText[] = []
  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function flush(): void {
    clearTimer()
    if (!pending.length) return
    const batch = pending
    pending = []
    // seq 有值时必须按 seq 升序回放，否则 reducer 会按去重规则丢掉后面的组。
    batch.sort((a, b) => {
      const sa = typeof a.event.seq === 'number' ? a.event.seq : Number.POSITIVE_INFINITY
      const sb = typeof b.event.seq === 'number' ? b.event.seq : Number.POSITIVE_INFINITY
      if (sa === sb) return 0
      return sa < sb ? -1 : 1
    })
    for (const item of batch) {
      options.onEvent({ ...item.event, delta: item.delta })
    }
  }

  function schedule(): void {
    if (flushMs === 0 || timer !== null) return
    timer = setTimeout(() => {
      timer = null
      flush()
    }, flushMs)
  }

  function push(event: HarnessEvent): void {
    if (event.type !== 'text_delta' || !event.delta) {
      flush()
      options.onEvent(event)
      return
    }
    const key = `${event.turn_id || ''}|${event.agent_id || ''}`
    const existing = pending.find(item => item.key === key)
    if (existing) {
      existing.event = event
      existing.delta += event.delta
    } else {
      pending.push({ key, event, delta: event.delta })
    }
    if (flushMs === 0) {
      flush()
      return
    }
    schedule()
  }

  function dispose(): void {
    clearTimer()
    pending = []
  }

  return { push, flush, dispose }
}

export interface TimelineItem {
  key: string
  kind: 'text' | 'tool' | 'reasoning' | 'notice'
  text?: string
  step?: AgentStep
  notice?: AgentOutputPart['notice']
}

function normalizeAnswer(text?: string): string {
  return (text || '').replace(/\s+/g, ' ').trim()
}

/**
 * 从消息的 agents[].parts 派生单一时间线，并判断最终答案是否已在时间线里。
 * 时间线已包含同一答案时，不再额外渲染「最终答案」块。
 */
export function useTimelineItems(message: () => MultiAgentMessage): {
  items: Ref<TimelineItem[]>
  isDuplicateAnswer: Ref<boolean>
} {
  const items = computed<TimelineItem[]>(() => {
    const out: TimelineItem[] = []
    for (const agent of message().agents) {
      const parts = normalizeAgent(agent).parts
      for (const part of parts) {
        // 隐藏 Harness 图内节点（无 tool_name），只保留真实工具调用。
        if (part.kind === 'tool' && part.step?.tool_name) {
          out.push({ key: `${agent.agent_id}:${part.step.step_id}`, kind: 'tool', step: part.step })
        } else if (part.kind === 'notice') {
          out.push({
            key: `${agent.agent_id}:notice:${part.seq}`,
            kind: 'notice',
            text: part.text,
            notice: part.notice,
          })
        } else if ((part.kind === 'text' || part.kind === 'reasoning') && (part.text || '').trim()) {
          out.push({
            key: `${agent.agent_id}:${part.seq}`,
            kind: part.kind === 'reasoning' ? 'reasoning' : 'text',
            text: part.text,
          })
        }
      }
    }
    return out
  })

  const isDuplicateAnswer = computed(() => {
    const content = normalizeAnswer(message().content)
    if (!content) return false
    const texts = items.value.filter(item => item.kind === 'text').map(item => item.text || '')
    if (!texts.length) return false
    return normalizeAnswer(texts.join('')) === content
  })

  return { items, isDuplicateAnswer }
}

export interface StickToBottomOptions {
  threshold?: number
  /** 流式跟随用 auto/instant，避免每个刷新都启动 smooth 动画。 */
  behavior?: ScrollBehavior
}

/**
 * 贴底才跟随的滚动控制：用户上滚后停止自动跟随，回到底部后恢复。
 */
export function useStickToBottom(
  el: Ref<HTMLElement | undefined>,
  source: () => unknown,
  options: StickToBottomOptions = {},
): {
  isNearBottom: Ref<boolean>
  onScroll: (event?: Event) => void
  scrollToBottom: (behavior?: ScrollBehavior) => void
} {
  const threshold = options.threshold ?? 100
  const behavior = options.behavior ?? 'auto'
  const isNearBottom = ref(true)

  function onScroll(event?: Event): void {
    if (event && 'isTrusted' in event && !event.isTrusted) return
    const node = el.value
    if (!node) return
    isNearBottom.value = node.scrollHeight - node.scrollTop - node.clientHeight < threshold
  }

  function scrollToBottom(next: ScrollBehavior = 'smooth'): void {
    const node = el.value
    if (!node) return
    isNearBottom.value = true
    node.scrollTo({ top: node.scrollHeight, behavior: next })
  }

  watch(source, async () => {
    if (!isNearBottom.value) return
    await nextTick()
    const node = el.value
    if (!node || !isNearBottom.value) return
    node.scrollTo({ top: node.scrollHeight, behavior })
  })

  return { isNearBottom, onScroll, scrollToBottom }
}
