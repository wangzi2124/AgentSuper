// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { useStickToBottom } from '@/v2/useStickToBottom'

function makeScroller(): HTMLElement {
  return {
    scrollHeight: 1000,
    clientHeight: 200,
    scrollTop: 800,
    scrollTo: vi.fn(),
  } as unknown as HTMLElement
}

function trustedScroll(): Event {
  return { isTrusted: true } as Event
}

describe('v2/useStickToBottom', () => {
  it('does not auto-scroll after the user scrolls away from bottom', async () => {
    const node = makeScroller()
    const source = ref(0)
    const { isNearBottom, onScroll } = useStickToBottom(ref<HTMLElement | null>(node), () => source.value)

    node.scrollTop = 100
    onScroll(trustedScroll())
    expect(isNearBottom.value).toBe(false)

    source.value += 1
    await nextTick()
    await nextTick()
    expect(node.scrollTo).not.toHaveBeenCalled()
  })

  it('follows streaming updates while near bottom', async () => {
    const node = makeScroller()
    const source = ref(0)
    const { onScroll } = useStickToBottom(ref<HTMLElement | null>(node), () => source.value)

    node.scrollTop = 750
    onScroll(trustedScroll())

    source.value += 1
    await nextTick()
    await nextTick()
    expect(node.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'auto' })
  })

  it('scrollToBottom restores stickiness and ignores untrusted scroll events', () => {
    const node = makeScroller()
    const source = ref(0)
    const { isNearBottom, onScroll, scrollToBottom } = useStickToBottom(ref<HTMLElement | null>(node), () => source.value)

    node.scrollTop = 0
    onScroll(trustedScroll())
    expect(isNearBottom.value).toBe(false)

    scrollToBottom('smooth')
    expect(isNearBottom.value).toBe(true)
    expect(node.scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' })

    node.scrollTop = 0
    onScroll(trustedScroll())
    expect(isNearBottom.value).toBe(true)

    onScroll({ isTrusted: false } as Event)
    expect(isNearBottom.value).toBe(true)
  })

  it('tracks near-top and scrollToTop returns to the beginning', () => {
    const node = makeScroller()
    const source = ref(0)
    const { isNearTop, onScroll, scrollToTop } = useStickToBottom(ref<HTMLElement | null>(node), () => source.value)

    node.scrollTop = 500
    onScroll(trustedScroll())
    expect(isNearTop.value).toBe(false)

    scrollToTop('auto')
    expect(isNearTop.value).toBe(true)
    expect(node.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
  })

  it('tracks scroll direction from trusted user scrolls only', () => {
    const node = makeScroller()
    const source = ref(0)
    const { scrollDirection, onScroll, scrollToBottom } = useStickToBottom(ref<HTMLElement | null>(node), () => source.value)

    onScroll(trustedScroll())
    expect(scrollDirection.value).toBe('none')

    node.scrollTop = 700
    onScroll(trustedScroll())
    expect(scrollDirection.value).toBe('up')

    node.scrollTop = 760
    onScroll(trustedScroll())
    expect(scrollDirection.value).toBe('down')

    scrollToBottom('auto')
    node.scrollTop = 0
    onScroll(trustedScroll())
    expect(scrollDirection.value).toBe('down')
  })
})
