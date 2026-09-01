/**
 * MultiAgentResponse 组件：按 agent 输出顺序交错渲染正文块与极简工具卡片
 * （对齐 opencode Part 渲染：text ↔ tool 交替，工具无参数/结果详情，最终答案在正文尾部）。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MultiAgentResponse from '@/components/MultiAgentResponse.vue'
import type { MultiAgentMessage } from '@/types'

const base = (agent: any): MultiAgentMessage => ({
  id: 'm1',
  role: 'assistant',
  content: '完整答案',
  agents: [{
    agent_id: 'rag',
    agent_name: '知识库检索',
    status: 'completed',
    content: '完整答案',
    steps: [],
    ...agent,
  }],
  timestamp: new Date(),
})

const step = (step_id: string, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  type: status === 'running' ? 'step_start' : 'step_end',
  step_id, name, status, ...extra,
})

describe('MultiAgentResponse 输出部件渲染', () => {
  it('按 parts 顺序交错渲染 text 与 tool（工具无参数/结果展开）', () => {
    const wrapper = mount(MultiAgentResponse, {
      props: {
        routingStatus: '',
        isLast: false,
        message: base({
          content: '正文两段+答案收尾',
          parts: [
            { seq: 0, kind: 'tool', step: step('s1', '读取文件', 'running', { tool_name: 'tool_read_file' }) },
            { seq: 1, kind: 'text', text: '第一段正文' },
            { seq: 2, kind: 'tool', step: step('s2', '写入文件', 'completed', { tool_name: 'tool_write_file', duration_ms: 1500 }) },
            { seq: 3, kind: 'text', text: '答案收尾' },
          ],
        }),
      },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })

    const tools = wrapper.findAll('.o-tool')
    const mds = wrapper.findAll('.o-text')
    // 顺序：tool(seq0) → text(seq1) → tool(seq2) → text(seq3)
    const order = wrapper.findAll('.o-parts > *').map(n => n.classes().includes('o-text') ? 'text' : 'tool')
    expect(order).toEqual(['tool', 'text', 'tool', 'text'])
    expect(tools).toHaveLength(2)
    expect(mds).toHaveLength(2)
    // 工具名去掉 tool_ 前缀（对齐 opencode 可读工具名）
    expect(tools[0].text()).toContain('read_file')
    expect(tools[1].text()).toContain('write_file')
    // 完成的工具带耗时，运行中的带状态符号
    expect(tools[1].text()).toContain('1.5s')
    expect(tools[0].classes()).toContain('running')
    // 正文渲染
    expect(mds[1].text()).toBe('答案收尾')
    // 绝不展示调用参数 JSON / 结果文本
    expect(wrapper.text()).not.toContain('"file"')
    expect(wrapper.text()).not.toContain('查看结果')
    expect(wrapper.text()).not.toContain('tool_args')
  })

  it('无 parts 时回退组装 steps + 正文（历史回放）', () => {
    const wrapper = mount(MultiAgentResponse, {
      props: {
        routingStatus: '',
        isLast: false,
        message: base({
          content: '历史答案',
          steps: [step('s1', '检索知识库', 'completed', { detail: '找到 3 条结果' })],
        }),
      },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    const order = wrapper.findAll('.o-parts > *').map(n => n.classes().includes('o-text') ? 'text' : 'tool')
    expect(order).toEqual(['tool', 'text'])
    expect(wrapper.findAll('.o-tool')).toHaveLength(1)
    expect(wrapper.findAll('.o-text')[0].text()).toBe('历史答案')
  })

  it('邻接正文段不重复渲染 a.content（避免"两条最终答案"）', () => {
    const wrapper = mount(MultiAgentResponse, {
      props: {
        routingStatus: '',
        isLast: false,
        message: base({
          parts: [{ seq: 0, kind: 'text', text: '唯一正文' }],
        }),
      },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    expect(wrapper.findAll('.o-text')).toHaveLength(1)
    expect(wrapper.findAll('.o-text')[0].text()).toBe('唯一正文')
    // 单 agent 内容与 message.content 一致 → 不重复展示 final answer
    expect(wrapper.findAll('.text .md-stub').length).toBe(1)
  })
})