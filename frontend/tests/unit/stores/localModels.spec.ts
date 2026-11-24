/**
 * localModels store：本地模型搜索分页 / 加载更多。
 * 覆盖 首页替换、命中来源固定、追加去重、到底停止。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  searchHubModels: vi.fn(),
  fetchLocalModelSources: vi.fn(),
  fetchLocalDownloads: vi.fn(),
}))

vi.mock('@/api/localModels', () => ({
  searchHubModels: mocks.searchHubModels,
  fetchLocalModelSources: mocks.fetchLocalModelSources,
  fetchLocalDownloads: mocks.fetchLocalDownloads,
  cancelLocalDownload: vi.fn(),
  createLocalDownload: vi.fn(),
  deleteLocalDownload: vi.fn(),
  fetchHubFiles: vi.fn(),
  retryLocalDownload: vi.fn(),
}))

vi.mock('@/stores/modelManager', () => ({
  useModelManagerStore: () => ({ load: vi.fn(), setNotice: vi.fn() }),
}))

import { useLocalModelsStore } from '@/stores/localModels'

function model(id: string, source = 'modelscope') {
  return { id, source, name: id, downloads: 1, tags: [] }
}

beforeEach(() => {
  setActivePinia(createPinia())
  mocks.fetchLocalModelSources.mockReset().mockResolvedValue({ endpoints: {}, priority: ['modelscope'] })
  mocks.fetchLocalDownloads.mockReset().mockResolvedValue([])
  mocks.searchHubModels.mockReset()
})

describe('localModels 分页', () => {
  it('search 取第一页并记录命中来源与 hasMore', async () => {
    mocks.searchHubModels.mockResolvedValue({
      models: [model('a'), model('b')], source: 'modelscope', hasMore: true, page: 1,
    })
    const store = useLocalModelsStore()
    store.query = 'qwen'
    store.source = 'auto'
    await store.search()
    expect(store.results.map(m => m.id)).toEqual(['a', 'b'])
    expect(store.activeSource).toBe('modelscope')
    expect(store.hasMore).toBe(true)
    expect(store.page).toBe(1)
  })

  it('loadMore 用命中来源请求下一页，追加并去重', async () => {
    mocks.searchHubModels
      .mockResolvedValueOnce({ models: [model('a')], source: 'modelscope', hasMore: true, page: 1 })
      .mockResolvedValueOnce({ models: [model('b'), model('a')], source: 'modelscope', hasMore: false, page: 2 })
    const store = useLocalModelsStore()
    store.query = 'qwen'
    await store.search()
    await store.loadMore()
    expect(store.results.map(m => m.id)).toEqual(['a', 'b'])
    expect(mocks.searchHubModels.mock.calls[1]).toEqual(['qwen', 'modelscope', 20, 2])
    expect(store.hasMore).toBe(false)
    expect(store.page).toBe(2)
  })

  it('没有更多时 loadMore 不再请求', async () => {
    mocks.searchHubModels.mockResolvedValue({ models: [model('a')], source: 'modelscope', hasMore: false, page: 1 })
    const store = useLocalModelsStore()
    store.query = 'qwen'
    await store.search()
    await store.loadMore()
    expect(mocks.searchHubModels).toHaveBeenCalledTimes(1)
  })

  it('search 重新检索会重置分页状态', async () => {
    mocks.searchHubModels
      .mockResolvedValueOnce({ models: [model('a')], source: 'modelscope', hasMore: true, page: 1 })
      .mockResolvedValueOnce({ models: [model('x')], source: 'huggingface', hasMore: false, page: 1 })
    const store = useLocalModelsStore()
    store.query = 'qwen'
    await store.search()
    expect(store.page).toBe(1)
    store.query = 'llama'
    await store.search()
    expect(store.results.map(m => m.id)).toEqual(['x'])
    expect(store.activeSource).toBe('huggingface')
    expect(store.hasMore).toBe(false)
  })
})
