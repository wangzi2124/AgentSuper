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

function submitButton(wrapper: ReturnType<typeof mount>) {
  const button = wrapper.findAll('button').find(item => item.text() === '提交')
  expect(button).toBeDefined()
  return button!
}

describe('FormCard 结构化问题卡', () => {
  it('展示推荐徽标并提交选中项', async () => {
    const wrapper = mount(FormCard, { props: { form: form() } })
    const options = wrapper.findAll('.v2-option')
    expect(options).toHaveLength(2)
    expect(options[0]!.text()).toContain('推荐')
    expect(options[0]!.text()).toContain('先写契约测试')

    await options[1]!.trigger('click')
    await submitButton(wrapper).trigger('click')

    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ answer: '直接实现' })
  })

  it('多选返回数组，数字键可选择选项', async () => {
    const multi = form()
    multi.fields = [{ ...multi.fields[0]!, multiple: true }]
    const wrapper = mount(FormCard, { props: { form: multi } })

    await wrapper.find('.v2-form').trigger('keydown', { key: '1' })
    await wrapper.find('.v2-form').trigger('keydown', { key: '2' })
    await submitButton(wrapper).trigger('click')

    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({
      answer: ['先补测试', '直接实现'],
    })
  })

  it('支持自定义答案与取消', async () => {
    const wrapper = mount(FormCard, { props: { form: form() } })

    await wrapper.find('.v2-custom').setValue('换一个方向')
    await submitButton(wrapper).trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ answer: '换一个方向' })

    await wrapper.find('button.danger').trigger('click')
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

    expect(wrapper.findAll('.v2-option')).toHaveLength(0)
    const select = wrapper.find('select')
    expect(select.exists()).toBe(true)
    await select.setValue('pnpm')
    await submitButton(wrapper).trigger('click')

    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ pm: 'pnpm' })
  })
})
