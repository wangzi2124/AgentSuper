import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MarkdownContent from '../../src/components/MarkdownContent.vue'

const viewPath = resolve(process.cwd(), 'src/views/V2ChatView.vue')

describe('chat markdown rendering', () => {
  it('renders fenced code blocks and lists', () => {
    const wrapper = mount(MarkdownContent, {
      props: {
        text: [
          '步骤：',
          '',
          '1. 创建文件',
          '2. 验证内容',
          '',
          '```ts',
          'const ok = true',
          '```',
        ].join('\n'),
      },
    })

    expect(wrapper.find('ol li').exists()).toBe(true)
    expect(wrapper.find('.md-code .md-pre code').text()).toContain('const ok = true')
    expect(wrapper.find('.md-code .md-code-lang').text()).toBe('ts')
  })

  it('wires MarkdownContent into the v2 assistant answer timeline', () => {
    const source = readFileSync(viewPath, 'utf8')
    expect(source).toContain("import MarkdownContent from '../components/MarkdownContent.vue'")
    expect(source).toMatch(/<MarkdownContent[^>]*?:text="part\.text"/)
  })
})
