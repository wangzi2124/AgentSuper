import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import FormCard from '@/v2/FormCard.vue'
import type { FormInfo } from '@/contracts/v2'

const form = (overrides: Partial<FormInfo> = {}): FormInfo => ({
  id: 'form_q',
  session_id: 's1',
  title: '下一步怎么做？',
  kind: 'question',
  header: '实现方向',
  question: '下一步怎么做？',
  state: 'pending',
  response: {},
  revision: 0,
  fields: [{
    id: 'answer',
    type: 'select',
    label: '下一步怎么做？',
    required: true,
    options: ['先补测试', '直接实现'],
    option_details: [
      { label: '先补测试', description: '先写契约测试', recommended: true },
      { label: '直接实现', description: '立即写代码', recommended: false },
    ],
    multiple: false,
    allow_custom: true,
  }],
  ...overrides,
})

describe('FormCard 快捷决策卡', () => {
  it('单选点击即提交，推荐项显示为主选项', async () => {
    const wrapper = mount(FormCard, { props: { form: form() } })
    const options = wrapper.findAll('.decision-option')
    expect(options).toHaveLength(3)
    expect(options[0]!.classes()).toContain('recommended')
    expect(options[0]!.text()).toContain('推荐')
    expect(options[0]!.text()).toContain('先写契约测试')
    expect(wrapper.find('.decision-submit').exists()).toBe(false)

    await options[1]!.trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ answer: '直接实现' })
  })

  it('数字键选择单选时立即提交', async () => {
    const wrapper = mount(FormCard, { props: { form: form() } })
    await wrapper.find('.decision-card').trigger('keydown', { key: '1' })
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ answer: '先补测试' })
  })

  it('多选可切换并需确认', async () => {
    const multi = form()
    multi.fields = [{ ...multi.fields[0]!, multiple: true }]
    const wrapper = mount(FormCard, { props: { form: multi } })

    await wrapper.find('.decision-card').trigger('keydown', { key: '1' })
    await wrapper.find('.decision-card').trigger('keydown', { key: '2' })
    expect(wrapper.emitted('submit')).toBeUndefined()

    const confirm = wrapper.find('.decision-submit')
    expect(confirm.text()).toContain('2')
    await confirm.trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({
      answer: ['先补测试', '直接实现'],
    })
  })

  it('自定义答案 Enter 提交，跳过按钮取消', async () => {
    const wrapper = mount(FormCard, { props: { form: form() } })
    await wrapper.findAll('.decision-option')[2]!.trigger('click')
    const input = wrapper.find('.decision-custom')
    expect(input.exists()).toBe(true)
    await input.setValue('换一个方向')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ answer: '换一个方向' })

    await wrapper.find('.decision-skip').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('兼容只有字符串 options 的旧表单', async () => {
    const legacy = form({
      kind: 'form',
      fields: [{
        id: 'pm',
        type: 'select',
        label: '包管理器',
        required: true,
        options: ['pnpm', 'npm'],
      }],
    })
    const wrapper = mount(FormCard, { props: { form: legacy } })

    const options = wrapper.findAll('.decision-option')
    expect(options).toHaveLength(2)
    await options[0]!.trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ pm: 'pnpm' })
  })
})
