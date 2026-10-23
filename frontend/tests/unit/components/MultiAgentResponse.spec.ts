/**
 * MultiAgentResponse 组件：按 agent 输出顺序交错渲染正文块与极简工具卡片
 * （对齐 opencode Part 渲染：text ↔ tool 交替，工具无参数/结果详情，最终答案在正文尾部）。
 */
import { describe, it, expect, vi } from 'vitest'
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
        running: true,
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
        running: true,
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
        running: true,
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

  // [P0 bug] 完成态折叠时，最终答案必须可见（单 Agent 内容相同也不能被吞掉）
  it('[P0] 折叠态最终答案始终可见（即使与单 Agent 内容相同）', () => {
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message: base({}) },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    // Agent 面板默认折叠
    expect(wrapper.find('.agents').exists()).toBe(false)
    expect(wrapper.findAll('.text .md-stub').map(n => n.text())).toEqual(['完整答案'])
  })

  it('[P0] 手动展开后：内容相同去重，内容不同仍展示最终答案', async () => {
    // 相同 → 展开后只保留 Agent 面板里的一份
    const same = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message: base({}) },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    await same.find('.thought-toggle').trigger('click')
    expect(same.findAll('.md-stub').map(n => n.text())).toEqual(['完整答案'])

    // 不同 → Agent 面板内容 + 最终答案都可见
    const msg = base({})
    msg.content = '最终答案'
    msg.agents[0].content = '执行过程'
    const diff = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message: msg },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    await diff.find('.thought-toggle').trigger('click')
    expect(diff.findAll('.md-stub').map(n => n.text())).toEqual(['执行过程', '最终答案'])
  })

  it('files_changed 渲染改动文件卡片（状态徽标 + 相对路径 + 增减行）', () => {
    const message = base({})
    message.files_changed = [
      { file: 'src/a.ts', status: 'modified', additions: 3, deletions: 1 },
      { file: 'src/b.txt', status: 'added', additions: 10, deletions: 0 },
      { file: 'img/logo.png', status: 'added', binary: true },
    ]
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    const rows = wrapper.findAll('.fc-row')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('修改')
    expect(rows[0].text()).toContain('src/a.ts')
    expect(rows[0].text()).toContain('+3')
    expect(rows[0].text()).toContain('-1')
    expect(rows[1].text()).toContain('新增')
    expect(rows[1].text()).toContain('+10')
    // 二进制文件不再显示行数，显示"二进制"
    expect(rows[2].text()).toContain('二进制')
    expect(rows[2].text()).not.toContain('+0')
    expect(wrapper.find('.fc-title').text()).toContain('文件改动')
  })

  it('无 files_changed 不渲染改动卡片', () => {
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message: base({}) },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    expect(wrapper.find('.files-changed').exists()).toBe(false)
  })

  it('[撤回改动] 文件卡片展示恢复按钮，点击 emit restore（外部文件标注外部路径）', async () => {
    const message = base({})
    message.files_changed = [
      { file: 'src/a.ts', status: 'modified', additions: 2, deletions: 0 },
      { file: 'C:\\Users\\me\\Desktop\\out.txt', status: 'modified', additions: 1, deletions: 1, external: true },
    ]
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    const btn = wrapper.find('.fc-restore')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('撤回本轮改动')
    expect(btn.attributes('disabled')).toBeUndefined()
    // 外部文件显示"外部路径"标注
    expect(wrapper.findAll('.fc-row')[1].text()).toContain('外部路径')
    await btn.trigger('click')
    expect(wrapper.emitted('restore')).toHaveLength(1)
  })

  it('[撤回改动] 已撤回消息按钮置灰显示"已撤回"', () => {
    const message = base({})
    message.files_changed = [{ file: 'src/a.ts', status: 'modified', additions: 1, deletions: 0 }]
    message.snapshotRestored = true
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    const btn = wrapper.find('.fc-restore')
    expect(btn.text()).toBe('已撤回')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.classes()).toContain('restored')
  })

  // [C8] 计划文件卡片：展示路径 + 点击复制（浏览器无法直接打开后端本地文件）
  it('plan_path 渲染计划文件卡片，点击复制路径并提示已复制', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const message = base({})
    message.plan_path = 'E:/AgentSuper/backend/data/plans/ses_1/plan.md'
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    const card = wrapper.find('.plan-file')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('已生成计划文件')
    expect(card.find('.pf-path').text()).toBe(message.plan_path)
    expect(card.text()).toContain('点击复制路径')
    await card.trigger('click')
    expect(writeText).toHaveBeenCalledWith(message.plan_path)
    expect(card.text()).toContain('已复制')
  })

  it('剪贴板不可用（非安全上下文）→ 选中路径文本 + toast 提示', async () => {
    const prompt = vi.fn()
    vi.stubGlobal('prompt', prompt)
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    const message = base({})
    message.plan_path = '/tmp/plan.md'
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    await wrapper.find('.plan-file').trigger('click')
    // 降级不再用 window.prompt，而是选中路径 + toast 提示手动复制
    expect(prompt).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('已选中路径')
    expect(wrapper.find('.plan-file').text()).toContain('点击复制路径')
    vi.unstubAllGlobals()
  })

  it('无 plan_path 不渲染计划文件卡片', () => {
    const wrapper = mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message: base({}) },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })
    expect(wrapper.find('.plan-file').exists()).toBe(false)
  })
})

/** [查看改动] 文件改动卡片可展开 unified diff（按需拉取 + 行级着色） */
describe('MultiAgentResponse 文件改动 diff 展开', () => {
  const withFiles = (extra: Partial<MultiAgentMessage> = {}): MultiAgentMessage => {
    const m = base({})
    m.files_changed = [
      { file: 'backend/app/x.py', status: 'modified', additions: 2, deletions: 1 },
      { file: 'backend/app/logo.png', status: 'added', binary: true },
      { file: 'E:/outside/y.py', status: 'modified', additions: 1, deletions: 0, external: true },
    ]
    return Object.assign(m, extra)
  }

  const mountIt = (
    message: MultiAgentMessage,
    loadDiff?: (id: string, step?: number) => Promise<unknown>,
  ) =>
    mount(MultiAgentResponse, {
      props: { routingStatus: '', isLast: false, message, loadDiff },
      global: { stubs: { MarkdownContent: { template: '<div class="md-stub">{{ text }}</div>', props: ['text'] } } },
    })

  it('默认收起；点击「查看 diff」才按需拉取并渲染行级着色', async () => {
    const loadDiff = vi.fn().mockResolvedValue({ files: [], truncated: false, reason: '' })
    const wrapper = mountIt(withFiles(), loadDiff)
    expect(wrapper.find('.fc-diff').exists()).toBe(false)
    expect(wrapper.find('.fc-restore').text()).toBe('撤回本轮改动')

    await wrapper.find('.fc-toggle').trigger('click')
    expect(loadDiff).toHaveBeenCalledWith('m1', undefined)
    expect(wrapper.find('.fc-diff').exists()).toBe(true)
    // 再次点击收起，且不重复请求
    await wrapper.find('.fc-toggle').trigger('click')
    expect(wrapper.find('.fc-diff').exists()).toBe(false)
    expect(loadDiff).toHaveBeenCalledTimes(1)
  })

  it('同一轮次重复展开使用组件内缓存，不再请求', async () => {
    const loadDiff = vi.fn().mockResolvedValue({
      files: [{
        file: 'backend/app/x.py',
        diff: [
          'diff --git a/backend/app/x.py b/backend/app/x.py',
          'index 111..222 100644',
          '--- a/backend/app/x.py',
          '+++ b/backend/app/x.py',
          '@@ -1,3 +1,4 @@',
          ' line1',
          '-line2',
          '+line2-changed',
          '+line3',
        ].join('\n'),
      }],
      truncated: false,
      reason: '',
      steps: 1,
    })
    const wrapper = mountIt(withFiles(), loadDiff)
    await wrapper.find('.fc-toggle').trigger('click')
    expect(loadDiff).toHaveBeenCalledTimes(1)
    const lines = wrapper.findAll('.fc-dl')
    expect(wrapper.find('.fc-diff-path').text()).toBe('backend/app/x.py')
    const cls = lines.map(n => n.classes()[1])
    expect(cls).toEqual(['meta', 'meta', 'meta', 'meta', 'hunk', 'ctx', 'del', 'add', 'add'])
    // 收起再展开：组件内已缓存，不再请求
    await wrapper.find('.fc-toggle').trigger('click')
    await wrapper.find('.fc-toggle').trigger('click')
    expect(loadDiff).toHaveBeenCalledTimes(1)
  })

  it('老消息无 after_tree → 展示 diffReason 而非报错', async () => {
    const loadDiff = vi.fn().mockResolvedValue({
      files: [],
      truncated: false,
      reason: '该消息为旧版本记录，无 after_tree，无法渲染 diff',
      steps: 0,
    })
    const wrapper = mountIt(withFiles(), loadDiff)
    await wrapper.find('.fc-toggle').trigger('click')
    expect(wrapper.find('.fc-diff-hint').text()).toContain('after_tree')
    expect(wrapper.find('.fc-diff-file').exists()).toBe(false)
  })

  it('只有 1 个写步骤时不显示步骤选择器', async () => {
    const loadDiff = vi.fn().mockResolvedValue({ files: [], truncated: false, reason: '', steps: 1 })
    const wrapper = mountIt(withFiles(), loadDiff)
    await wrapper.find('.fc-toggle').trigger('click')
    expect(wrapper.find('.fc-steps').exists()).toBe(false)
  })

  it('多步骤 → 步骤选择器可切到单步并回传 step 下标', async () => {
    const loadDiff = vi.fn().mockResolvedValue({ files: [], truncated: false, reason: '', steps: 3 })
    const wrapper = mountIt(withFiles(), loadDiff)
    await wrapper.find('.fc-toggle').trigger('click')
    // 整轮先拉一次（不带 step）
    expect(loadDiff).toHaveBeenNthCalledWith(1, 'm1', undefined)

    const steps = wrapper.findAll('.fc-step')
    expect(steps.map(n => n.text())).toEqual(['整轮', '第 1 步', '第 2 步', '第 3 步'])
    // 「整轮」默认高亮
    expect(steps[0].classes()).toContain('active')

    await steps[3].trigger('click') // [3] = 第 3 步 → step=2
    expect(loadDiff).toHaveBeenNthCalledWith(2, 'm1', 2)

    await wrapper.findAll('.fc-step')[0].trigger('click') // 回到整轮
    expect(loadDiff).toHaveBeenNthCalledWith(3, 'm1', undefined)
  })

  it('步骤选择器：拉取失败显示错误且不崩', async () => {
    const loadDiff = vi.fn()
      .mockResolvedValueOnce({ files: [], truncated: false, reason: '', steps: 2 })
      .mockRejectedValueOnce(new Error('该轮次缺少逐步快照'))
    const wrapper = mountIt(withFiles(), loadDiff)
    await wrapper.find('.fc-toggle').trigger('click')
    await wrapper.findAll('.fc-step')[1].trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.fc-diff-hint').text()).toContain('缺少逐步快照')
  })

  it('拉取失败 → 显示错误提示且不崩', async () => {    const loadDiff = vi.fn().mockRejectedValue(new Error('会话尚未在服务器创建'))
    const wrapper = mountIt(withFiles(), loadDiff)
    await wrapper.find('.fc-toggle').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.fc-diff-hint').text()).toContain('会话尚未在服务器创建')
  })

  it('无 loadDiff 注入时安全降级（不请求、不报错）', async () => {
    const wrapper = mountIt(withFiles())
    await wrapper.find('.fc-toggle').trigger('click')
    expect(wrapper.find('.fc-diff').exists()).toBe(true)
    expect(wrapper.find('.fc-diff-hint').text()).toBe('无可展示的 diff')
  })

  it('diff 截断提示 + 撤回后按钮禁用', async () => {
    const loadDiff = vi.fn().mockResolvedValue({ files: [], truncated: true, reason: '', steps: 0 })
    const wrapper = mountIt(withFiles({ snapshotRestored: true }), loadDiff)
    const restore = wrapper.find('.fc-restore')
    expect(restore.text()).toBe('已撤回')
    expect(restore.attributes('disabled')).toBeDefined()
    await wrapper.find('.fc-toggle').trigger('click')
    // 无 diff 文件时先提示「无可展示的 diff」，截断提示作为附加说明一并渲染
    const hints = wrapper.findAll('.fc-diff-hint').map(n => n.text())
    expect(hints).toContain('无可展示的 diff')
    expect(hints).toContain('diff 过长已截断')
  })

  it('文件行渲染状态徽标 / 二进制 / 外部路径标记', () => {
    const wrapper = mountIt(withFiles())
    const badges = wrapper.findAll('.fc-badge').map(n => n.text())
    expect(badges).toEqual(['修改', '新增', '修改'])
    expect(wrapper.find('.fc-lines.binary').exists()).toBe(true)
    expect(wrapper.text()).toContain('外部路径')
    expect(wrapper.text()).toContain('+2')
    expect(wrapper.text()).toContain('-1')
  })
})
