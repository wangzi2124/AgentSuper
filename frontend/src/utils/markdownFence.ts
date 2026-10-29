const FENCE_RE = /^[ \t]{0,3}(`{3,}|~{3,})(.*)$/

/**
 * 返回最后一个未闭合代码围栏的起始偏移；全部闭合时返回 -1。
 * 用于流式期间把未闭合代码块降级为纯文本，避免对半截代码反复跑 hljs。
 */
export function findUnclosedFenceStart(src: string): number {
  if (!src) return -1
  let open: { char: string; length: number; start: number } | null = null
  let offset = 0
  const lines = src.split('\n')
  for (const line of lines) {
    const match = FENCE_RE.exec(line)
    if (match) {
      const char = match[1][0]
      const length = match[1].length
      const rest = match[2]
      if (!open) {
        open = { char, length, start: offset }
      } else if (char === open.char && length >= open.length && !rest.trim()) {
        open = null
      }
    }
    offset += line.length + 1
  }
  return open ? open.start : -1
}

/** 拆分出「已闭合的安全前缀」与「未闭合围栏尾部」。 */
export function splitUnclosedFence(src: string): { safe: string; tail: string } | null {
  const start = findUnclosedFenceStart(src)
  if (start < 0) return null
  return { safe: src.slice(0, start), tail: src.slice(start) }
}
