import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

vi.mock('@/stores/modelManager', () => {
  const model = (id: string, name: string, origin: string) => ({
    id,
    name,
    origin,
    provider: id.split('/')[0],
    family: id,
    description: '',
    context_length: 1000000,
    limits: { max_output_tokens: 64000 },
    capabilities: { tool_use: true, vision: false, reasoning: false },
    cost: { input_per_1m: 0, output_per_1m: 0, cache_read_per_1m: 0, cache_write_per_1m: 0 },
  })
  const fake = {
    config: {
      models: [
        model('builtin/x', 'Builtin X', 'builtin'),
        model('p/seen', 'Provider Seen', 'provider'),
        model('custom/manual', 'Custom Manual', 'extra'),
        model('builtin/edited', 'Edited Builtin', 'override'),
        model('ollama_chat/local', 'Local Ollama', 'local'),
      ],
      providers: [],
      default_model: null,
      small_model: null,
      image_caption_model: null,
      voice_model_size: null,
      source_path: 'data/model_catalog.db',
    },
    loading: false,
    saving: false,
    refreshing: false,
    busyId: null,
    error: '',
    notice: '',
    load: vi.fn(),
    setDefaults: vi.fn(),
    updateProvider: vi.fn(),
    removeProvider: vi.fn(),
    addOrUpdateModel: vi.fn(),
    removeModel: vi.fn(),
    setError: vi.fn(),
  }
  return { useModelManagerStore: () => fake }
})

import ModelManagerView from '@/views/ModelManagerView.vue'

describe('ModelManagerView 模型列表', () => {
  it('只展示手动/Provider 模型，内置与本机 Ollama 探测不进入表格', () => {
    const wrapper = mount(ModelManagerView)

    const tables = wrapper.findAll('.mm-table')
    const modelTable = tables[tables.length - 1]

    expect(modelTable.text()).toContain('p/seen')
    expect(modelTable.text()).toContain('custom/manual')
    expect(modelTable.text()).toContain('builtin/edited')
    expect(modelTable.text()).not.toContain('builtin/x')
    expect(modelTable.text()).not.toContain('ollama_chat/local')
    expect(wrapper.text()).toContain('共 3 个模型')
  })

  it('重新添加已有模型 ID 时自动带出目录参数', async () => {
    const wrapper = mount(ModelManagerView)
    const addBtn = wrapper.findAll('button').find(b => b.text() === '+ 自定义模型')
    expect(addBtn).toBeTruthy()
    await addBtn!.trigger('click')

    await wrapper.find('input[placeholder="ollama/qwen2.5-coder:latest"]').setValue('builtin/x')
    await nextTick()

    expect((wrapper.find('input[placeholder="Ollama Qwen2.5-Coder"]').element as HTMLInputElement).value)
      .toBe('Builtin X')
    expect((wrapper.find('input[type="number"][min="1024"]').element as HTMLInputElement).value)
      .toBe('1000000')
    expect((wrapper.find('input[type="number"][min="256"]').element as HTMLInputElement).value)
      .toBe('64000')
  })
})
