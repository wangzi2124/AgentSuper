import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const calls: Array<{ url: string; init?: RequestInit }> = []

function response(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? 'OK' : 'ERROR',
    json: async () => body,
  } as Response
}

vi.mock('@/api/fetch', () => ({
  fetchWithTimeout: vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    if (url.includes('/servers/remote/auth')) {
      return response({ authorization_url: 'https://auth.example.com/authorize' })
    }
    if (url.includes('/prompts/get')) {
      return response({
        prompt: { messages: [{ role: 'user', content: { type: 'text', text: 'Hello Alice' } }] },
      })
    }
    if (url.endsWith('/prompts')) {
      return response({
        prompts: [{ name: 'greet', arguments: [{ name: 'name', required: true }] }],
      })
    }
    return response({
      servers: [
        {
          name: 'demo',
          transport: 'stdio',
          enabled: true,
          status: 'connected',
          error: '',
          tools: [],
          resources: [],
          prompts: [
            {
              name: 'greet',
              description: 'Greeting prompt',
              arguments: [{ name: 'name', description: 'who', required: true }],
            },
          ],
          registered: [],
          has_instructions: false,
        },
        {
          name: 'remote',
          transport: 'http',
          enabled: true,
          status: 'auth_required',
          error: 'OAuth authorization required',
          tools: [],
          resources: [],
          prompts: [],
          registered: [],
          has_instructions: false,
          oauth: true,
          authorized: false,
          auth_required: true,
        },
      ],
    })
  }),
}))

import McpSettingsView from '@/views/McpSettingsView.vue'

describe('McpSettingsView prompts', () => {
  beforeEach(() => {
    calls.length = 0
  })

  it('列出 prompts，按参数调用并展示返回消息', async () => {
    const wrapper = mount(McpSettingsView)
    await flushPromises()

    expect(wrapper.text()).toContain('greet')
    expect(wrapper.text()).toContain('Greeting prompt')

    const input = wrapper.find('.prompt-args input')
    expect(input.exists()).toBe(true)
    await input.setValue('Alice')

    const getBtn = wrapper.findAll('.prompt-head button').find(b => b.text() === '获取')
    expect(getBtn).toBeTruthy()
    await getBtn!.trigger('click')
    await flushPromises()

    const call = calls.find(c => c.url.includes('/prompts/get'))
    expect(call).toBeTruthy()
    expect(JSON.parse(String(call!.init?.body))).toEqual({
      prompt: 'greet',
      arguments: { name: 'Alice' },
    })
    expect(wrapper.find('.prompt-result').text()).toContain('Hello Alice')
  })

  it('starts OAuth for an HTTP server', async () => {
    const popup = {
      closed: false,
      opener: window,
      location: { href: '' },
      close: vi.fn(),
    } as unknown as Window
    const open = vi.spyOn(window, 'open').mockReturnValue(popup)
    const wrapper = mount(McpSettingsView)
    await flushPromises()

    const authBtn = wrapper.findAll('button').find(b => b.text() === 'OAuth 授权')
    expect(authBtn).toBeTruthy()
    await authBtn!.trigger('click')
    await flushPromises()

    const call = calls.find(c => c.url.includes('/servers/remote/auth'))
    expect(call).toBeTruthy()
    expect(call!.init?.method).toBe('POST')
    expect(open).toHaveBeenCalledWith('about:blank', '_blank')
    expect((popup.location as Location).href).toBe('https://auth.example.com/authorize')
    expect(popup.opener).toBeNull()
    wrapper.unmount()
    open.mockRestore()
  })
})
