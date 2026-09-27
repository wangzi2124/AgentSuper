/**
 * logs store：全链路日志页的状态与请求参数。
 * 覆盖 列表/条目 两种视图、筛选下推、分页、链路下钻与失败降级。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  listTraces: vi.fn(),
  listEntries: vi.fn(),
  getTrace: vi.fn(),
  getStats: vi.fn(),
  getFilterOptions: vi.fn(),
  cleanupLogs: vi.fn(),
  clearLogs: vi.fn(),
}))

vi.mock('@/api/logs', () => ({
  listTraces: mocks.listTraces,
  listEntries: mocks.listEntries,
  getTrace: mocks.getTrace,
  getStats: mocks.getStats,
  getFilterOptions: mocks.getFilterOptions,
  cleanupLogs: mocks.cleanupLogs,
  clearLogs: mocks.clearLogs,
  STAGES: ['http', 'agent', 'tool', 'llm', 'system'],
  STAGE_LABELS: {},
  LEVELS: ['INFO', 'ERROR'],
  LEVEL_LABELS: {},
}))

import { useLogsStore } from '@/stores/logs'

function traceRow(id: string) {
  return {
    trace_id: id, start_ts: 1, end_ts: 2, duration_ms: 1, nodes: 3,
    errors: 0, warnings: 0, session_id: 's1', user_id: 'u1', path: '/api/chat', title: 't',
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  mocks.listTraces.mockReset().mockResolvedValue({ traces: [traceRow('t1')], total: 1, offset: 0, limit: 30 })
  mocks.listEntries.mockReset().mockResolvedValue({ entries: [], total: 0, offset: 0, limit: 30 })
  mocks.getTrace.mockReset().mockResolvedValue({ trace_id: 't1', entries: [], total: 0 })
  mocks.getStats.mockReset().mockResolvedValue({
    total: 3, traces: 1, by_level: { ERROR: 1 }, by_stage: {}, by_component: {},
    by_agent: {}, avg_duration_ms: 12.5, slow_traces: [], top_errors: [],
    counters: { queued: 0, queue_size: 5000, dropped: 0, errors: 0, written: 3 },
  })
  mocks.getFilterOptions.mockReset().mockResolvedValue({
    components: ['supervisor'], agents: ['build'], sessions: ['s1'], stages: [], levels: [],
  })
  mocks.cleanupLogs.mockReset().mockResolvedValue({ expired: 2, overflow: 1 })
  mocks.clearLogs.mockReset().mockResolvedValue({ deleted: 5 })
})

describe('logs store', () => {
  it('init 拉取链路列表 + 统计 + 筛选项', async () => {
    const store = useLogsStore()
    await store.init()

    expect(mocks.listTraces).toHaveBeenCalledWith({}, 0, 30)
    expect(store.traces).toHaveLength(1)
    expect(store.total).toBe(1)
    expect(store.stats?.by_level.ERROR).toBe(1)
    expect(store.options?.components).toEqual(['supervisor'])
  })

  it('setFilter 合并筛选条件、回第一页并同步刷新统计', async () => {
    const store = useLogsStore()
    await store.init()
    await store.setFilter('stage', 'llm')
    await store.setFilter('level', 'ERROR')

    expect(store.filters).toEqual({ stage: 'llm', level: 'ERROR' })
    expect(store.hasFilters).toBe(true)
    expect(store.offset).toBe(0)
    expect(mocks.listTraces).toHaveBeenLastCalledWith({ stage: 'llm', level: 'ERROR' }, 0, 30)
    expect(mocks.getStats).toHaveBeenLastCalledWith({ stage: 'llm', level: 'ERROR' })
  })

  it('setFilter 传空值视为清除该条件', async () => {
    const store = useLogsStore()
    await store.setFilter('stage', 'llm')
    await store.setFilter('stage', '')

    expect(store.filters.stage).toBeUndefined()
    expect(store.hasFilters).toBe(false)
  })

  it('has_error=false 不作为筛选条件下发（false 会被后端忽略）', async () => {
    const store = useLogsStore()
    await store.setFilter('has_error', true)
    expect(store.filters.has_error).toBe(true)
    await store.setFilter('has_error', false)
    expect(store.filters.has_error).toBeUndefined()
  })

  it('resetFilters 清空条件并回到链路视图', async () => {
    const store = useLogsStore()
    await store.setFilter('stage', 'llm')
    await store.setView('entries')
    expect(store.view).toBe('entries')

    await store.resetFilters()
    expect(store.filters).toEqual({})
    expect(store.view).toBe('traces')
    expect(store.offset).toBe(0)
  })

  it('setView 切到 entries 改调 listEntries', async () => {
    const store = useLogsStore()
    await store.init()
    await store.setView('entries')

    expect(mocks.listEntries).toHaveBeenCalledWith({}, 0, 30)
    expect(mocks.listTraces).toHaveBeenCalledTimes(1)
  })

  it('分页按 limit 步进，越界时不发请求', async () => {
    mocks.listTraces.mockResolvedValue({ traces: [], total: 100, offset: 0, limit: 30 })
    const store = useLogsStore()
    await store.init()
    expect(store.pageCount).toBe(4)

    await store.nextPage()
    expect(store.offset).toBe(30)
    expect(store.page).toBe(2)
    expect(mocks.listTraces).toHaveBeenLastCalledWith({}, 30, 30)

    // 末页 next 无效
    mocks.listTraces.mockResolvedValue({ traces: [], total: 100, offset: 90, limit: 30 })
    await store.nextPage()
    await store.nextPage()
    expect(store.canNext).toBe(false)
    expect(store.offset).toBe(90)
  })

  it('changeLimit 重置到第一页', async () => {
    mocks.listTraces.mockResolvedValue({ traces: [], total: 500, offset: 0, limit: 30 })
    const store = useLogsStore()
    await store.init()
    await store.nextPage()
    await store.changeLimit(100)

    expect(store.offset).toBe(0)
    expect(mocks.listTraces).toHaveBeenLastCalledWith({}, 0, 100)
  })

  it('openTrace 拉取时间线，closeTrace 复位', async () => {
    const store = useLogsStore()
    await store.openTrace('t1')
    expect(store.activeTraceId).toBe('t1')
    expect(mocks.getTrace).toHaveBeenCalledWith('t1')

    store.closeTrace()
    expect(store.activeTraceId).toBe('')
    expect(store.detail).toBeNull()
  })

  it('列表请求失败时降级为空列表并展示错误，不抛出', async () => {
    mocks.listTraces.mockRejectedValue(new Error('boom'))
    const store = useLogsStore()
    await store.init()

    expect(store.listError).toBe('boom')
    expect(store.traces).toEqual([])
    expect(store.total).toBe(0)
    expect(store.listLoading).toBe(false)
  })

  it('时间线加载失败只提示，不清空列表', async () => {
    mocks.getTrace.mockRejectedValue(new Error('404'))
    const store = useLogsStore()
    await store.init()
    await store.openTrace('nope')

    expect(store.detailError).toBe('404')
    expect(store.traces).toHaveLength(1)
  })

  it('cleanup 后刷新列表与统计', async () => {
    const store = useLogsStore()
    await store.init()
    const before = mocks.listTraces.mock.calls.length
    const res = await store.cleanup()

    expect(res).toEqual({ expired: 2, overflow: 1 })
    expect(mocks.listTraces.mock.calls.length).toBe(before + 1)
    expect(mocks.getStats.mock.calls.length).toBeGreaterThan(1)
  })

  it('clear 当前链路时自动收起时间线', async () => {
    const store = useLogsStore()
    await store.openTrace('t1')
    await store.clear({ trace_id: 't1' })

    expect(mocks.clearLogs).toHaveBeenCalledWith({ trace_id: 't1' })
    expect(store.activeTraceId).toBe('')
    expect(store.offset).toBe(0)
  })

  it('clear 全量清空也复位分页', async () => {
    const store = useLogsStore()
    await store.init()
    await store.clear({ all: true })
    expect(mocks.clearLogs).toHaveBeenCalledWith({ all: true })
    expect(store.offset).toBe(0)
  })
})
