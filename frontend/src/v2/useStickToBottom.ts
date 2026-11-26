import { nextTick, ref, watch, type Ref } from 'vue'

export interface StickToBottomOptions {
  threshold?: number
  behavior?: ScrollBehavior
}

export function useStickToBottom(
  el: Ref<HTMLElement | null>,
  source: () => unknown,
  options: StickToBottomOptions = {},
) {
  const threshold = options.threshold ?? 100
  const behavior = options.behavior ?? 'auto'
  const isNearTop = ref(true)
  const isNearBottom = ref(true)
  const scrollDirection = ref<'up' | 'down' | 'none'>('none')
  let lastScrollTop: number | null = null
  let programmaticScroll = false
  let programmaticTimer: ReturnType<typeof setTimeout> | null = null

  function finishProgrammatic(): void {
    programmaticScroll = false
    programmaticTimer = null
    measure()
  }

  function measure(): void {
    const node = el.value
    if (!node) return
    isNearTop.value = node.scrollTop < threshold
    isNearBottom.value = node.scrollHeight - node.scrollTop - node.clientHeight < threshold
  }

  function onScroll(event?: Event): void {
    if (event && 'isTrusted' in event && !event.isTrusted) return
    if (programmaticScroll) return
    measure()
    const node = el.value
    if (!node) return
    const current = node.scrollTop
    if (lastScrollTop !== null) {
      const delta = current - lastScrollTop
      if (Math.abs(delta) > 1) scrollDirection.value = delta > 0 ? 'down' : 'up'
    }
    lastScrollTop = current
  }

  function beginProgrammatic(next: ScrollBehavior): void {
    programmaticScroll = true
    if (programmaticTimer !== null) clearTimeout(programmaticTimer)
    programmaticTimer = setTimeout(finishProgrammatic, next === 'smooth' ? 1000 : 60)
  }

  function scrollToBottom(next: ScrollBehavior = 'smooth'): void {
    const node = el.value
    if (!node) return
    isNearBottom.value = true
    beginProgrammatic(next)
    node.scrollTo({ top: node.scrollHeight, behavior: next })
  }

  function scrollToTop(next: ScrollBehavior = 'smooth'): void {
    const node = el.value
    if (!node) return
    isNearTop.value = true
    beginProgrammatic(next)
    node.scrollTo({ top: 0, behavior: next })
  }

  watch(source, async () => {
    if (!isNearBottom.value) return
    await nextTick()
    const node = el.value
    if (!node || !isNearBottom.value) return
    node.scrollTo({ top: node.scrollHeight, behavior })
  })

  return { isNearTop, isNearBottom, scrollDirection, onScroll, scrollToBottom, scrollToTop }
}
