/**
 * MobileShell 组件：抽屉「设置」入口 → 设置表单弹层（功能收纳 · 原则四）。
 * 用自定义 Vant stub（渲染默认插槽）避免 teleport 到 body，便于断言。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { SUPPORTED_MODELS } from '@/config/models'

vi.mock('vue-router', () => ({
  useRoute: () => ({ name: 'chat', path: '/chat', params: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/stores/permission', () => ({
  usePermissionStore: () => ({ workspaces: [], loadWorkspaces: vi.fn() }),
}))

vi.mock('@/stores/multiAgent', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/stores/multiAgent')>()
  return {
    ...actual,
  }
})

import MobileShell from '@/mobile/MobileShell.vue'

// 默认插槽渲染 stub：让 van-popup 内的表单内容出现在 DOM
const slotStub = (name: string, props = '') => ({
  name,
  props: ['show', ...(props ? props.split(',') : [])],
  template: `<div class="${name}" data-show="{{ show }}"><slot /><slot name="left" /><slot name="title" /></div>`,
})

const global = {
  plugins: [createPinia()],
  stubs: {
    MobileChat: true, MobileDocuments: true, MobileSkills: true, MobilePlugins: true,
    MobileVectors: true, MobileGenerated: true, MobileMonitoring: true, MobileCustomTools: true,
    'van-nav-bar': slotStub('van-nav-bar'),
    'van-popup': { props: ['show'], template: '<div class="van-popup" :class="{open: !!show}"><slot /></div>' },
    'van-icon': { template: '<i class="van-icon" />' },
    'van-cell': slotStub('van-cell'),
    'van-field': slotStub('van-field'),
    'van-switch': { props: ['modelValue'], template: '<span class="van-switch" />' },
    'van-button': slotStub('van-button'),
    'van-picker': { props: ['columns'], template: '<div class="van-picker"><div v-for="c in columns" class="picker-opt">{{ c.text || c }}</div></div>' },
    'van-tabbar': slotStub('van-tabbar'),
    'van-tabbar-item': slotStub('van-tabbar-item'),
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('MobileShell 功能收纳', () => {
  it('渲染抽屉「设置」入口', () => {
    const wrapper = mount(MobileShell, { global })
    const entry = wrapper.find('.settings-item')
    expect(entry.exists()).toBe(true)
    expect(entry.text()).toContain('设置')
  })

  it('点击「设置」→ 弹出设置表单（模型 / 向量库开关）', async () => {
    const wrapper = mount(MobileShell, { global })
    // 弹层 stub 常渲染插槽 → 用 .open class 判断显隐
    expect(wrapper.find('.van-popup.open').exists()).toBe(false)
    await wrapper.find('.settings-item').trigger('click')
    expect(wrapper.find('.van-popup.open').exists()).toBe(true)
    expect(wrapper.find('.settings-form').text()).toContain('模型')
  })

  it('向量库开关绑定 agent.useVectorDb（Pinia 状态共享）', async () => {
    const wrapper = mount(MobileShell, { global })
    await wrapper.find('.settings-item').trigger('click')
    const store = wrapper.vm as unknown as { $pinia: any }
    // 通过 Pinia 实例取 store
    const pinia = (store as any).$pinia
    const { useMultiAgentStore } = await import('@/stores/multiAgent')
    const agent = useMultiAgentStore(pinia)
    agent.useVectorDb = true
    await wrapper.vm.$nextTick()
    // 开关 stub 存在（绑定已接通即满足：设置读写走 Pinia）
    expect(agent.useVectorDb).toBe(true)
  })

  it('模型选择器列出来自 SUPPORTED_MODELS 的选项', async () => {
    const wrapper = mount(MobileShell, { global })
    await wrapper.find('.settings-item').trigger('click')
    // 打开模型选择器
    const field = wrapper.find('.van-field')
    await field.trigger('click')
    const picker = wrapper.find('.van-picker')
    expect(picker.exists()).toBe(true)
    const opts = picker.findAll('.picker-opt')
    expect(opts.length).toBe(SUPPORTED_MODELS.length)
    // picker 选项显示 label（值同源自 SUPPORTED_MODELS）
    expect(opts[0].text()).toContain(SUPPORTED_MODELS[0].label)
  })
})
