import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({ fetchStats: vi.fn() }))

vi.mock('@/api/monitor', () => ({ fetchStats: mocks.fetchStats }))

import MonitoringView from '@/views/MonitoringView.vue'
import type { MonitorStats } from '@/types'

const stats: MonitorStats = {
  service: {
    name: 'agentsuper-backend',
    pid: 123,
    available: true,
    timestamp: 1,
    uptime_seconds: 90,
    cpu_percent: 12.5,
    memory_rss_bytes: 256 * 1024 * 1024,
    memory_vms_bytes: 512 * 1024 * 1024,
    memory_percent: 3.2,
    host_cpu_percent: 18.4,
    host_memory_total_bytes: 8 * 1024 * 1024 * 1024,
    host_memory_used_bytes: 4 * 1024 * 1024 * 1024,
    host_memory_percent: 50,
  },
  requests: { total: 0, by_path: {}, by_status: {} },
  model_calls: {
    total: 0,
    by_model: {},
    total_prompt_tokens: 0,
    total_completion_tokens: 0,
    total_duration_ms: 0,
    avg_duration_ms: 0,
    tool_rounds_total: 0,
    avg_tool_rounds: 0,
  },
}

describe('MonitoringView 服务资源', () => {
  it('只显示服务 CPU / 内存，不显示 LLM / HTTP 统计', async () => {
    mocks.fetchStats.mockResolvedValue(stats)
    const wrapper = mount(MonitoringView)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('运行监控')
    expect(text).toContain('服务资源')
    expect(text).toContain('进程 CPU')
    expect(wrapper.find('.svc-cpu-value').text()).toBe('12.5%')
    expect(wrapper.find('.svc-mem-value').text()).toBe('3.2%')
    expect(text).toContain('256.0 MB')
    expect(text).not.toContain('LLM 调用')
    expect(text).not.toContain('HTTP 请求')
    expect(text).not.toContain('状态码')
    wrapper.unmount()
  })
})
