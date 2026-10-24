import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

const FILES = [
  'src/components/ConversationTimeline.vue',
  'src/components/TimelineToolRow.vue',
  'src/components/PermissionInlineCard.vue',
]

describe('harness chat copy', () => {
  it('does not ship ? placeholders for visible Chinese copy', () => {
    const bad: string[] = []
    for (const file of FILES) {
      const lines = readFileSync(file, 'utf8').split('\n')
      lines.forEach((line, i) => {
        if (/[\u4e00-\u9fff]/.test(line) && /\?{2,}/.test(line)) {
          bad.push(`${file}:${i + 1}: ${line.trim()}`)
        }
      })
    }
    expect(bad).toEqual([])
  })
})
